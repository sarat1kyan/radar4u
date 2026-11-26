"""Search orchestration.

intent -> eligible providers -> concurrent queries (bounded by a deadline) ->
normalise -> dedupe -> strict evaluation -> rank -> grouped outcome.

A failing or slow provider never fails the search: its error is recorded and
reported, the other providers' results are still shown. Progress callbacks
fire as each provider finishes so the bot can update a message while the
rest are still running. Abandoned searches are cancelled via task cancel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from radar.core.errors import NotConfigured, ProviderError
from radar.core.money import ConvertedMoney
from radar.domain.common import Domain
from radar.domain.intents import SearchIntent
from radar.domain.results import Result
from radar.providers.base import Provider, SearchContext
from radar.providers.fx import FxService
from radar.providers.registry import PARTNER_ONLY, ProviderRegistry
from radar.search.dedup import dedupe
from radar.search.filters import Tier, evaluate
from radar.search.metrics import METRICS
from radar.search.ranking import Ranked, rank

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, int, int], Awaitable[None]]


@dataclass
class SearchOutcome:
    intent: SearchIntent
    ranked: list[Ranked] = field(default_factory=list)
    provider_errors: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    queried: list[str] = field(default_factory=list)
    partner_only: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    total_ms: float = 0.0
    timed_out: bool = False

    def tier(self, t: Tier) -> list[Ranked]:
        return [r for r in self.ranked if r.tier is t]

    @property
    def exact(self) -> list[Ranked]:
        return self.tier(Tier.EXACT)

    @property
    def unverified(self) -> list[Ranked]:
        return self.tier(Tier.UNVERIFIED)

    @property
    def alternatives(self) -> list[Ranked]:
        return self.tier(Tier.ALTERNATIVE)

    @property
    def no_coverage(self) -> bool:
        return not self.queried

    def find(self, result_id: str) -> Ranked | None:
        return next((r for r in self.ranked if r.result.id == result_id), None)


class SearchPipeline:
    def __init__(self, registry: ProviderRegistry, fx: FxService | None = None, timeout_s: float = 25.0) -> None:
        self.registry = registry
        self.fx = fx
        self.timeout_s = timeout_s

    def _sub_intents(self, intent: SearchIntent) -> list[SearchIntent]:
        """Multimodal journeys also ask flight providers for the same origin and destination."""
        subs = [intent]
        if intent.domain is Domain.GROUND and "mode:multimodal" in intent.categories:
            flight = intent.model_copy(deep=True)
            flight.domain = Domain.FLIGHT
            flight.requirements = [c for c in flight.requirements if Domain.FLIGHT in c.definition.domains]
            flight.preferences = [c for c in flight.preferences if Domain.FLIGHT in c.definition.domains]
            subs.append(flight)
        return subs

    async def run(
        self,
        intent: SearchIntent,
        ctx: SearchContext,
        *,
        on_progress: ProgressCb | None = None,
        monitoring: bool = False,
        only_providers: set[str] | None = None,
    ) -> SearchOutcome:
        started = time.monotonic()
        out = SearchOutcome(intent=intent)
        calls: list[tuple[Provider, SearchIntent]] = []
        for sub in self._sub_intents(intent):
            el = self.registry.eligible(sub, monitoring=monitoring)
            out.skipped.update(el.skipped)
            for p in el.providers:
                if only_providers is None or p.name in only_providers:
                    calls.append((p, sub))
            out.partner_only += PARTNER_ONLY.get(sub.domain, [])
        out.queried = [p.name for p, _ in calls]
        METRICS.inc("searches", domain=intent.domain.value)
        if not calls:
            METRICS.inc("searches_no_coverage", domain=intent.domain.value)
            out.total_ms = (time.monotonic() - started) * 1000
            return out

        if ctx.deadline is None:
            ctx.deadline = datetime.now(UTC) + timedelta(seconds=self.timeout_s)
        results: list[Result] = []
        done_count = 0

        async def call(p: Provider, sub: SearchIntent) -> None:
            nonlocal done_count
            t0 = time.monotonic()
            try:
                rs = await p.search(sub, ctx)
                results.extend(rs)
                METRICS.inc("provider_calls", provider=p.name, outcome="ok")
            except NotConfigured:
                out.provider_errors[p.name] = "not_configured"
            except ProviderError as exc:
                out.provider_errors[p.name] = exc.code
                METRICS.inc("provider_calls", provider=p.name, outcome=exc.code)
            except Exception:  # an adapter bug must not take the search down
                log.exception("provider %s crashed", p.name)
                out.provider_errors[p.name] = "internal_error"
                METRICS.inc("provider_calls", provider=p.name, outcome="internal_error")
            finally:
                ms = (time.monotonic() - t0) * 1000
                out.timings_ms[p.name] = ms
                METRICS.observe("provider_latency_ms", ms, provider=p.name)
                done_count += 1
                if on_progress is not None:
                    try:
                        await on_progress(p.name, done_count, len(calls))
                    except Exception:
                        log.debug("progress callback failed", exc_info=True)

        tasks = [asyncio.create_task(call(p, sub), name=f"search:{p.name}") for p, sub in calls]
        remaining = max(1.0, (ctx.deadline - datetime.now(UTC)).total_seconds())
        _, pending = await asyncio.wait(tasks, timeout=remaining)
        if pending:
            out.timed_out = True
            for t in pending:
                t.cancel()
                name = t.get_name().split(":", 1)[1]
                out.provider_errors.setdefault(name, "timeout")
            await asyncio.gather(*pending, return_exceptions=True)

        merged = dedupe(results)
        ranked: list[Ranked] = []
        for r in merged:
            conv = await self._convert(r, ctx.currency)
            budget_conv = None
            if intent.budget.max is not None and r.price is not None:
                if r.price.amount.currency == intent.budget.max.currency:
                    budget_conv = r.price.amount
                elif self.fx is not None:
                    c = await self.fx.convert(r.price.amount, intent.budget.max.currency)
                    budget_conv = c.converted if c else None
            ranked.append(Ranked(result=r, report=evaluate(r, intent, budget_conv), converted=conv))
        out.ranked = rank(ranked, intent)
        out.total_ms = (time.monotonic() - started) * 1000
        METRICS.observe("search_latency_ms", out.total_ms, domain=intent.domain.value)
        METRICS.inc("results", len(out.ranked), domain=intent.domain.value)
        return out

    async def _convert(self, r: Result, currency: str) -> ConvertedMoney | None:
        if self.fx is None or r.price is None or r.price.amount.currency == currency.upper():
            return None
        try:
            return await self.fx.convert(r.price.amount, currency)
        except ProviderError:
            return None
