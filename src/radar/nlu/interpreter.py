"""Turns a message into a validated, location-resolved SearchIntent.

Backend selection: the rule parser always runs first (cheap, offline,
deterministic). A configured LLM backend is consulted only when the rule result
is weak, and its output must survive the same validation. Any LLM failure
falls back to the rule result; the user never waits on a broken model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from radar.config import Settings
from radar.core.timeutil import now_in, tz
from radar.domain.common import LocationRef, Place
from radar.domain.intents import SearchIntent
from radar.geo.resolver import GeoResolver
from radar.nlu.llm.base import LLMBackend, LLMUnavailable, to_search_intent
from radar.nlu.refine import is_followup, refine
from radar.nlu.rules import _required_missing, parse

log = logging.getLogger(__name__)

LLM_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class UserContext:
    language: str = "en"
    currency: str = "EUR"
    timezone: str | None = None
    home: Place | None = None
    # Set only after the user explicitly shared location for this session.
    current: Place | None = None
    favorites: list[Place] = field(default_factory=list)

    @property
    def anchor(self) -> Place | None:
        return self.current or self.home


@dataclass
class Interpretation:
    intent: SearchIntent
    source: str
    confidence: float
    refined: bool = False
    used_context_location: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.intent.missing)


def build_llm(settings: Settings) -> LLMBackend | None:
    if settings.nlu_backend == "anthropic" and settings.anthropic_api_key and settings.anthropic_model:
        from radar.nlu.llm.anthropic_backend import AnthropicBackend

        return AnthropicBackend(settings.anthropic_api_key.get_secret_value(), settings.anthropic_model)
    if settings.nlu_backend == "openai" and settings.openai_model:
        from radar.nlu.llm.openai_compat import OpenAICompatBackend

        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        return OpenAICompatBackend(settings.openai_base_url, settings.openai_model, key)
    return None


class Interpreter:
    def __init__(self, resolver: GeoResolver, llm: LLMBackend | None = None) -> None:
        self.resolver = resolver
        self.llm = llm

    def _today(self, ctx: UserContext) -> tuple[date, str]:
        zone = (ctx.anchor.timezone if ctx.anchor else None) or ctx.timezone or "UTC"
        return now_in(zone).date(), zone

    async def interpret(
        self,
        text: str,
        ctx: UserContext,
        previous: SearchIntent | None = None,
        *,
        today: date | None = None,
        online: bool = True,
    ) -> Interpretation:
        text = text.strip()[:2000]
        base_today, zone = self._today(ctx)
        today = today or base_today

        fresh = parse(text, today=today, zone=zone, ui_language=ctx.language)
        if previous is not None and is_followup(text, previous, fresh):
            intent = refine(previous, text, today=today, zone=zone)
            result = Interpretation(intent=intent, source="rules", confidence=1.0, refined=True)
        else:
            result = Interpretation(intent=fresh.intent, source="rules", confidence=fresh.confidence)
            if self.llm is not None and (
                fresh.confidence < LLM_CONFIDENCE_THRESHOLD or "domain" in fresh.intent.missing
            ):
                llm_intent = await self._llm(text, today, zone, ctx.language, previous)
                if llm_intent is not None:
                    result = Interpretation(intent=llm_intent, source=self.llm.name, confidence=0.8)

        await self._complete(result, ctx, online=online)
        return result

    async def _llm(
        self, text: str, today: date, zone: str, ui_language: str, previous: SearchIntent | None
    ) -> SearchIntent | None:
        assert self.llm is not None
        try:
            li = await self.llm.extract(text, today=today, zone=zone, previous=previous)
        except LLMUnavailable as exc:
            log.info("llm backend unavailable, using rules: %s", exc)
            return None
        except Exception:  # never let a backend bug take the bot down
            log.exception("llm backend failed")
            return None
        intent = to_search_intent(li, text, zone, ui_language)
        if intent is not None:
            intent.missing = _required_missing(intent)
        return intent

    async def _complete(self, result: Interpretation, ctx: UserContext, *, online: bool) -> None:
        intent = result.intent
        anchor = ctx.anchor
        # Fill gaps from authorized context instead of asking.
        if anchor is not None:
            if intent.domain.is_transport and intent.origin is None and intent.destination is not None:
                intent.origin = LocationRef(text=anchor.name, place=anchor)
                result.used_context_location = True
            elif not intent.domain.is_transport and intent.location is None and intent.destination is None:
                intent.location = LocationRef(text=anchor.name, place=anchor)
                result.used_context_location = True

        for attr in ("location", "origin", "destination"):
            ref: LocationRef | None = getattr(intent, attr)
            if ref is None or ref.resolved:
                continue
            bias = anchor.country_code if anchor else None
            resolved = await self.resolver.resolve(ref.text, language=ctx.language, country_bias=bias, online=online)
            if resolved.place is None:
                result.notes.append(f"unresolved:{attr}:{ref.text}")
                setattr(intent, attr, None)
            else:
                resolved.radius_m = ref.radius_m
                setattr(intent, attr, resolved)

        _reanchor_timezone(intent)
        intent.missing = _required_missing(intent)
        if any(n.startswith("unresolved:") for n in result.notes):
            for n in result.notes:
                if n.startswith("unresolved:"):
                    key = n.split(":")[1]
                    key = "location" if key == "location" else key
                    if key not in intent.missing:
                        intent.missing.append(key)


def _reanchor_timezone(intent: SearchIntent) -> None:
    """Times the user typed are wall-clock times at the place they are about."""
    ref = intent.origin if intent.domain.is_transport else (intent.location or intent.destination)
    zone = ref.place.timezone if ref and ref.place else None
    if not zone or intent.when is None:
        return

    z = tz(zone)

    def fix(dt: datetime | None) -> datetime | None:
        return dt.replace(tzinfo=z) if dt is not None else None

    intent.when = intent.when.model_copy(update={"start": fix(intent.when.start), "end": fix(intent.when.end)})
