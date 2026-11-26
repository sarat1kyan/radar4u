from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from radar.core.errors import QuotaExceeded, ValidationFailed
from radar.core.money import Money
from radar.domain.intents import MonitorKind, MonitorSpec
from radar.monitoring.engine import Notifier, RadarDraft, RadarEngine, RadarService, backoff
from radar.nlu.rules import parse
from radar.storage import repo
from radar.storage.models import Notification, Radar

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def intent(text):
    return parse(text, today=NOW.date()).intent


async def make_user(db, **kw):
    async with db.session() as s:
        u = await repo.get_or_create_user(s, 1001, "en")
        for k, v in kw.items():
            setattr(u, k, v)
    return u


async def render(user, batch):
    return " | ".join(f"{n.kind}:{n.payload['title']}:{(n.payload['new'] or {}).get('amount')}" for n in batch), None


async def test_price_radar_full_cycle(db, pipeline, settings, fixtures, sender):
    user = await make_user(db, currency="EUR")
    svc = RadarService(db, pipeline, settings)
    i = intent("hotel in Berlin from December 21 to December 23 for two")
    radar = await svc.create(
        user,
        RadarDraft(i, MonitorSpec(kind=MonitorKind.PRICE_BELOW, threshold=Money.of(150, "EUR")), "Berlin"),
        now=NOW,
    )
    assert radar.interval_s >= 600 and radar.providers == ["fixtures"]
    clock = {"now": NOW}
    engine = RadarEngine(db, pipeline, settings, clock=lambda: clock["now"])

    fixtures.price_shift = Decimal(100)  # everything above threshold
    assert await engine.tick() == 1
    async with db.session() as s:
        assert (await s.execute(select(Notification))).scalars().all() == []
        r = await s.get(Radar, radar.id)
        assert r.checks == 1 and r.next_check_at.replace(tzinfo=UTC) > NOW

    fixtures.price_shift = Decimal(-60)  # drop below 150 total for 2 nights for some stays
    clock["now"] = NOW + timedelta(hours=2)
    assert await engine.tick() == 1
    async with db.session() as s:
        alerts = (await s.execute(select(Notification))).scalars().all()
    assert alerts and all(n.kind == "price_drop" for n in alerts)
    assert all(Decimal(n.payload["new"]["amount"]) <= 150 for n in alerts)
    assert all(n.payload["old"] is not None for n in alerts)  # previous value is shown
    assert all(n.payload["freshness"] == "fixture" for n in alerts)

    # same state again: no duplicate alerts
    clock["now"] = NOW + timedelta(hours=4)
    await engine.tick()
    async with db.session() as s:
        assert len((await s.execute(select(Notification))).scalars().all()) == len(alerts)

    notifier = Notifier(db, sender, render, clock=lambda: clock["now"])
    sent = await notifier.deliver()
    assert sent == len(alerts) and len(sender.sent) == (len(alerts) if len(alerts) <= 3 else 1)
    assert await notifier.deliver() == 0


async def test_new_results_baseline_is_silent(db, pipeline, settings, sender):
    user = await make_user(db)
    svc = RadarService(db, pipeline, settings)
    radar = await svc.create(
        user,
        RadarDraft(
            intent("long-term apartment in Tbilisi"), MonitorSpec(kind=MonitorKind.NEW_RESULTS), "Tbilisi flats"
        ),
        now=NOW,
    )
    engine = RadarEngine(db, pipeline, settings, clock=lambda: NOW)
    await engine.tick()
    async with db.session() as s:
        assert (await s.execute(select(Notification))).scalars().all() == []
        assert len(await repo.observations(s, radar.id)) == 5


async def test_quiet_hours_delay_delivery(db, pipeline, settings, fixtures, sender):
    user = await make_user(db, timezone="Asia/Yerevan", quiet_start=time(22, 0), quiet_end=time(8, 0))
    svc = RadarService(db, pipeline, settings)
    await svc.create(
        user,
        RadarDraft(
            intent("hotel in Berlin from December 21 to December 23"),
            MonitorSpec(kind=MonitorKind.PRICE_BELOW, threshold=Money.of(5000, "EUR")),
            "x",
        ),
        now=NOW,
    )
    night = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)  # 00:00 in Yerevan
    engine = RadarEngine(db, pipeline, settings, clock=lambda: night)
    await engine.tick()
    notifier = Notifier(db, sender, render, clock=lambda: night)
    assert await notifier.deliver() == 0
    morning = datetime(2026, 11, 27, 4, 30, tzinfo=UTC)  # 08:30 in Yerevan
    notifier.clock = lambda: morning
    assert await notifier.deliver() > 0


async def test_quota_and_unsupported(db, pipeline, settings):
    user = await make_user(db)
    settings.max_radars_per_user = 1
    svc = RadarService(db, pipeline, settings)
    i = intent("hotel in Berlin from December 21 to December 23")
    await svc.create(user, RadarDraft(i, MonitorSpec(kind=MonitorKind.AVAILABILITY), "a"), now=NOW)
    with pytest.raises(QuotaExceeded):
        await svc.create(user, RadarDraft(i, MonitorSpec(kind=MonitorKind.AVAILABILITY), "b"), now=NOW)
    with pytest.raises(ValidationFailed):
        await svc.create(
            user, RadarDraft(intent("sushi in Yerevan"), MonitorSpec(kind=MonitorKind.NEW_RESULTS), "c"), now=NOW
        )


async def test_failures_back_off_then_error_state(db, settings):
    from radar.config import Settings
    from radar.core.errors import ProviderUnavailable
    from radar.domain.common import Domain
    from radar.providers.base import AccessLevel, Capability, Provider
    from radar.providers.registry import ProviderRegistry
    from radar.search.pipeline import SearchPipeline

    class Down(Provider):
        name = "down"
        access = AccessLevel.SELF_SERVE_KEY
        capabilities = (Capability(Domain.STAY, monitoring_allowed=True, min_monitor_interval_s=600),)

        async def search(self, intent, ctx):
            raise ProviderUnavailable("down", "x")

    pipe = SearchPipeline(ProviderRegistry([Down()], Settings(RADAR_ENV="test")), timeout_s=5)
    user = await make_user(db)
    svc = RadarService(db, pipe, settings)
    radar = await svc.create(
        user,
        RadarDraft(
            intent("hotel in Berlin from December 21 to December 23"), MonitorSpec(kind=MonitorKind.AVAILABILITY), "x"
        ),
        now=NOW,
    )
    t = NOW
    for _ in range(5):
        engine = RadarEngine(db, pipe, settings, clock=lambda t=t: t)
        await engine.tick()
        async with db.session() as s:
            r = await s.get(Radar, radar.id)
            t = r.next_check_at.replace(tzinfo=UTC)
    assert r.state == "error" and r.error_count == 5 and r.last_error == "provider_unavailable"


def test_backoff_grows_and_caps():
    assert backoff(600, 0).total_seconds() <= 600
    assert backoff(600, 20).total_seconds() <= 86400


async def test_radar_expires_with_its_date(db, pipeline, settings, sender):
    user = await make_user(db)
    svc = RadarService(db, pipeline, settings)
    radar = await svc.create(
        user,
        RadarDraft(
            intent("hotel in Berlin from December 21 to December 23"), MonitorSpec(kind=MonitorKind.AVAILABILITY), "x"
        ),
        now=NOW,
    )
    later = datetime(2026, 12, 22, tzinfo=UTC)
    await RadarEngine(db, pipeline, settings, clock=lambda: later).tick()
    async with db.session() as s:
        assert (await s.get(Radar, radar.id)).state == "expired"


async def test_forget_user_removes_everything(db, pipeline, settings):
    user = await make_user(db)
    svc = RadarService(db, pipeline, settings)
    await svc.create(
        user,
        RadarDraft(
            intent("hotel in Berlin from December 21 to December 23"), MonitorSpec(kind=MonitorKind.AVAILABILITY), "x"
        ),
        now=NOW,
    )
    async with db.session() as s:
        u = await repo.get_or_create_user(s, 1001)
        await repo.forget_user(s, u)
    async with db.session() as s:
        assert (await s.execute(select(Radar))).scalars().all() == []
