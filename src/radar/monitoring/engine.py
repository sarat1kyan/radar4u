"""Radar lifecycle: creation rules, scheduled checks, alert creation, delivery.

Scheduling: each radar has next_check_at. A worker tick claims due radars
(SKIP LOCKED on PostgreSQL so several workers can run), runs the search in
monitoring mode (only providers whose terms allow scheduled queries), stores
observations, turns material changes into notifications and reschedules with
jitter. Failures back off exponentially with jitter, and after repeated
failures the radar enters the error state, still retried but slower.

Delivery: notifications wait for the user's quiet hours to end, are grouped
per user when several arrive together, and digest-mode users get one message.
Nothing is ever booked or bought by a radar.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol

from radar.config import Settings, get_settings
from radar.core.errors import QuotaExceeded, ValidationFailed
from radar.core.money import Money
from radar.core.provenance import utcnow
from radar.core.timeutil import aware, next_after_quiet, tz
from radar.domain.intents import MonitorKind, MonitorSpec, SearchIntent
from radar.monitoring.diff import STALE_AFTER_CHECKS, Event, PrevObs, comparable_price, detect
from radar.providers.base import SearchContext
from radar.search.metrics import METRICS
from radar.search.pipeline import SearchPipeline
from radar.storage import repo
from radar.storage.db import Database
from radar.storage.models import Notification, Radar, RadarObservation, User

log = logging.getLogger(__name__)

MAX_BACKOFF = timedelta(hours=24)
ERROR_STATE_AFTER = 5
DEFAULT_LIFETIME = timedelta(days=30)


class Sender(Protocol):
    async def send(self, telegram_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> bool: ...


Renderer = Callable[[User, list[Notification]], Awaitable[tuple[str, list[list[tuple[str, str]]] | None]]]


def jitter(seconds: float, fraction: float = 0.1) -> timedelta:
    return timedelta(seconds=seconds * (1 + random.uniform(-fraction, fraction)))  # noqa: S311


def backoff(interval_s: int, errors: int) -> timedelta:
    base = min(MAX_BACKOFF.total_seconds(), interval_s * (2 ** min(errors, 10)))
    return timedelta(seconds=random.uniform(base / 2, base))  # noqa: S311 - full jitter


def radar_expiry(intent: SearchIntent, now: datetime) -> datetime:
    """A radar never outlives the thing it watches."""
    cap = now + DEFAULT_LIFETIME
    d = intent.stay.check_in if intent.stay else (intent.when.start if intent.when and intent.when.start else None)
    if d is None:
        return cap
    when = d if isinstance(d, datetime) else datetime.combine(d, time(0, 0), tz("UTC"))
    return min(cap, aware(when))


@dataclass
class RadarDraft:
    intent: SearchIntent
    monitor: MonitorSpec
    name: str


class RadarService:
    def __init__(self, db: Database, pipeline: SearchPipeline, settings: Settings | None = None) -> None:
        self.db = db
        self.pipeline = pipeline
        self.settings = settings or get_settings()

    def interval_for(self, intent: SearchIntent) -> int:
        """Slowest of: our floor, every eligible provider's permitted rate. Pricier sources go slower."""
        el = self.pipeline.registry.eligible(intent, monitoring=True)
        if el.empty:
            return 0
        floor = self.pipeline.registry.min_interval_s(intent)
        cost = sum(c.cost_units for p in el.providers for c in p.capabilities if c.domain is intent.domain)
        return int(max(floor, self.settings.min_radar_interval_s * max(1.0, cost)))

    async def create(self, user: User, draft: RadarDraft, now: datetime | None = None) -> Radar:
        now = now or utcnow()
        if draft.monitor.kind is MonitorKind.PRICE_BELOW and draft.monitor.threshold is None:
            raise ValidationFailed("price radar needs a threshold", code="threshold_missing")
        interval = self.interval_for(draft.intent)
        if interval == 0:
            raise ValidationFailed("no provider permits monitoring this search", code="monitoring_not_supported")
        async with self.db.session() as s:
            u = await s.get(User, user.id)
            assert u is not None
            if await repo.count_active_radars(s, u) >= self.settings.max_radars_per_user:
                raise QuotaExceeded("radar quota reached", code="radar_quota")
            providers = [p.name for p in self.pipeline.registry.eligible(draft.intent, monitoring=True).providers]
            th = draft.monitor.threshold
            radar = Radar(
                user_id=u.id,
                name=draft.name[:200],
                domain=draft.intent.domain.value,
                intent=draft.intent.model_dump(mode="json"),
                monitor_kind=draft.monitor.kind.value,
                threshold_amount=str(th.amount) if th else None,
                threshold_currency=th.currency if th else None,
                state="active",
                interval_s=interval,
                providers=providers,
                notify_mode=u.digest_mode,
                next_check_at=now,
                expires_at=radar_expiry(draft.intent, now),
            )
            s.add(radar)
            await s.flush()
            await repo.audit(s, u.id, "radar.create", radar_id=radar.id, domain=radar.domain, interval_s=interval)
            return radar

    async def set_state(self, user: User, radar_id: int, state: str) -> Radar | None:
        assert state in ("active", "paused", "deleted")
        async with self.db.session() as s:
            u = await s.get(User, user.id)
            assert u is not None
            r = await repo.get_radar(s, u, radar_id)
            if r is None:
                return None
            r.state = state
            if state == "active":
                r.error_count = 0
                r.next_check_at = utcnow()
            await repo.audit(s, u.id, f"radar.{state}", radar_id=radar_id)
            return r

    async def update_threshold(self, user: User, radar_id: int, threshold: Money) -> Radar | None:
        async with self.db.session() as s:
            u = await s.get(User, user.id)
            assert u is not None
            r = await repo.get_radar(s, u, radar_id)
            if r is None:
                return None
            r.threshold_amount, r.threshold_currency = str(threshold.amount), threshold.currency
            r.monitor_kind = MonitorKind.PRICE_BELOW.value
            return r


class RadarEngine:
    def __init__(
        self,
        db: Database,
        pipeline: SearchPipeline,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self.pipeline = pipeline
        self.settings = settings or get_settings()
        self.clock = clock

    async def tick(self) -> int:
        now = self.clock()
        async with self.db.session() as s:
            radars = await repo.due_radars(s, now, self.settings.monitor_batch, postgres=self.db.is_postgres)
            for r in radars:
                await self._check(s, r, now)
            return len(radars)

    async def _check(self, s: Any, radar: Radar, now: datetime) -> None:
        user = await s.get(User, radar.user_id)
        if aware(radar.expires_at) <= now:
            radar.state = "expired"
            await repo.add_notification(
                s,
                Notification(
                    user_id=radar.user_id,
                    radar_id=radar.id,
                    kind="expired",
                    payload={"name": radar.name},
                    dedupe_key=f"r{radar.id}:expired",
                    deliver_after=now,
                ),
            )
            return
        intent = SearchIntent.model_validate(radar.intent)
        threshold = (
            Money.of(radar.threshold_amount, radar.threshold_currency)
            if radar.threshold_amount and radar.threshold_currency
            else None
        )
        ctx = SearchContext(
            user_id=radar.user_id,
            locale=user.language if user else "en",
            currency=(threshold.currency if threshold else (user.currency if user else "EUR")),
            monitoring=True,
        )
        radar.last_check_at = now
        outcome = await self.pipeline.run(intent, ctx, monitoring=True, only_providers=set(radar.providers) or None)
        METRICS.inc("radar_checks", domain=radar.domain)
        if outcome.no_coverage or (outcome.queried and set(outcome.provider_errors) >= set(outcome.queried)):
            radar.error_count += 1
            radar.last_error = ",".join(sorted(set(outcome.provider_errors.values()))) or "no_coverage"
            if radar.error_count >= ERROR_STATE_AFTER:
                radar.state = "error"
            radar.next_check_at = now + backoff(radar.interval_s, radar.error_count)
            METRICS.inc("radar_check_failures", domain=radar.domain)
            return

        prev_rows = await repo.observations(s, radar.id)
        previous = {
            k: PrevObs(
                Decimal(o.price_amount) if o.price_amount else None, o.price_currency, o.availability, o.missing_checks
            )
            for k, o in prev_rows.items()
        }
        events = detect(
            MonitorKind(radar.monitor_kind), threshold, previous, outcome.ranked, baseline=radar.checks == 0
        )
        self._store_observations(s, radar, prev_rows, outcome.ranked, threshold, now)
        for ev in events:
            await self._notify(s, radar, user, ev, now)
        radar.checks += 1
        radar.error_count = 0
        radar.last_error = None
        radar.state = "active"
        radar.last_success_at = now
        radar.next_check_at = now + jitter(radar.interval_s)

    def _store_observations(
        self,
        s: Any,
        radar: Radar,
        prev_rows: dict[str, RadarObservation],
        ranked: list[Any],
        threshold: Money | None,
        now: datetime,
    ) -> None:
        seen = set()
        for item in ranked[:50]:
            key = item.result.id
            seen.add(key)
            price = comparable_price(item, threshold.currency if threshold else None)
            row = prev_rows.get(key)
            if row is None:
                row = RadarObservation(
                    radar_id=radar.id,
                    result_key=key,
                    title=item.result.title[:300],
                    provider=item.result.provenance.provider,
                    availability="",
                    history=[],
                    first_seen_at=now,
                )
                s.add(row)
            if price is not None:
                changed = row.price_amount != str(price.amount)
                row.price_amount, row.price_currency = str(price.amount), price.currency
                if changed:
                    row.history = [
                        *row.history[-29:],
                        {"at": now.isoformat(), "amount": str(price.amount), "currency": price.currency},
                    ]
            row.availability = item.result.availability.value
            row.last_seen_at = now
            row.missing_checks = 0
        for key, row in prev_rows.items():
            if key not in seen:
                row.missing_checks = min(row.missing_checks + 1, STALE_AFTER_CHECKS + 10)

    async def _notify(self, s: Any, radar: Radar, user: User | None, ev: Event, now: datetime) -> None:
        if ev.kind == "gone" or user is None:
            return
        r = ev.ranked
        result = r.result if r else None
        payload: dict[str, Any] = {
            "radar_id": radar.id,
            "name": radar.name,
            "title": ev.title,
            "old": ev.old.model_dump(mode="json") if ev.old else None,
            "new": ev.new.model_dump(mode="json") if ev.new else None,
            "source": result.provenance.attribution or result.provenance.provider if result else None,
            "freshness": result.provenance.freshness.value if result else None,
            "retrieved_at": result.provenance.retrieved_at.isoformat() if result else None,
            "met": r.report.met if r else [],
            "link": next((lk.url for lk in result.links if lk.url.startswith("https://")), None) if result else None,
        }
        value = str(ev.new.amount) if ev.new else ""
        local_now = now.astimezone(tz(user.timezone))
        deliver_local = next_after_quiet(local_now, user.quiet_start, user.quiet_end)
        digest = radar.notify_mode in ("daily", "weekly")
        if digest:
            deliver_local = _next_digest_slot(local_now, radar.notify_mode)
        await repo.add_notification(
            s,
            Notification(
                user_id=user.id,
                radar_id=radar.id,
                kind=ev.kind,
                payload=payload,
                dedupe_key=f"r{radar.id}:{ev.kind}:{ev.key}:{value}"[:200],
                digest=digest,
                deliver_after=deliver_local.astimezone(tz("UTC")),
            ),
        )
        METRICS.inc("radar_alerts", kind=ev.kind)


def _next_digest_slot(local_now: datetime, mode: str) -> datetime:
    slot = local_now.replace(hour=9, minute=0, second=0, microsecond=0)
    if slot <= local_now:
        slot += timedelta(days=1)
    if mode == "weekly":
        while slot.weekday() != 0:
            slot += timedelta(days=1)
    return slot


class Notifier:
    """Delivers pending notifications, grouped per user."""

    def __init__(self, db: Database, sender: Sender, render: Renderer, clock: Callable[[], datetime] = utcnow):
        self.db = db
        self.sender = sender
        self.render = render
        self.clock = clock

    async def deliver(self) -> int:
        now = self.clock()
        sent = 0
        async with self.db.session() as s:
            pending = await repo.pending_notifications(s, now)
            by_user: dict[int, list[Notification]] = defaultdict(list)
            for n in pending:
                by_user[n.user_id].append(n)
            for user_id, items in by_user.items():
                user = await s.get(User, user_id)
                if user is None:
                    continue
                # Up to 3 separate alerts; beyond that, one grouped message.
                batches = [[n] for n in items] if len(items) <= 3 and not any(n.digest for n in items) else [items]
                for batch in batches:
                    text, buttons = await self.render(user, batch)
                    ok = await self.sender.send(user.telegram_id, text, buttons)
                    for n in batch:
                        n.attempts += 1
                        if ok:
                            n.sent_at = now
                    sent += len(batch) if ok else 0
        METRICS.inc("notifications_sent", sent)
        return sent
