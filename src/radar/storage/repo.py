"""Data access helpers. Thin on purpose: business rules live in services."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from radar.core.provenance import utcnow
from radar.core.timeutil import aware
from radar.domain.common import Place
from radar.domain.intents import SearchIntent
from radar.storage.models import (
    AuditLog,
    Booking,
    Itinerary,
    Notification,
    Radar,
    RadarObservation,
    SearchSession,
    User,
)

SESSION_TTL = timedelta(days=30)


async def get_or_create_user(s: AsyncSession, telegram_id: int, language: str | None = None) -> User:
    user = (await s.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, language=(language or "en")[:2], favorites=[])
        s.add(user)
        await s.flush()
    else:
        user.last_seen_at = utcnow()
    return user


async def get_user(s: AsyncSession, user_id: int) -> User | None:
    return await s.get(User, user_id)


def user_home(user: User) -> Place | None:
    return Place.model_validate(user.home_place) if user.home_place else None


async def save_search(
    s: AsyncSession, user: User, intent: SearchIntent, results: list[dict[str, Any]], summary: dict[str, Any]
) -> SearchSession:
    row = SearchSession(user_id=user.id, intent=intent.model_dump(mode="json"), results=results, summary=summary)
    s.add(row)
    await s.flush()
    # Keep only the most recent few sessions per user.
    old = (
        (
            await s.execute(
                select(SearchSession.id)
                .where(SearchSession.user_id == user.id)
                .order_by(SearchSession.id.desc())
                .offset(5)
            )
        )
        .scalars()
        .all()
    )
    if old:
        await s.execute(delete(SearchSession).where(SearchSession.id.in_(old)))
    return row


async def last_search(s: AsyncSession, user: User) -> SearchSession | None:
    row = (
        await s.execute(
            select(SearchSession).where(SearchSession.user_id == user.id).order_by(SearchSession.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None and aware(row.created_at) < utcnow() - SESSION_TTL:
        return None
    return row


async def get_search(s: AsyncSession, user: User, session_id: int) -> SearchSession | None:
    row = await s.get(SearchSession, session_id)
    return row if row is not None and row.user_id == user.id else None


async def count_active_radars(s: AsyncSession, user: User) -> int:
    rows = (
        (
            await s.execute(
                select(Radar.id).where(Radar.user_id == user.id, Radar.state.in_(("active", "paused", "error")))
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def list_radars(s: AsyncSession, user: User) -> list[Radar]:
    return list(
        (await s.execute(select(Radar).where(Radar.user_id == user.id, Radar.state != "deleted").order_by(Radar.id)))
        .scalars()
        .all()
    )


async def get_radar(s: AsyncSession, user: User, radar_id: int) -> Radar | None:
    r = await s.get(Radar, radar_id)
    return r if r is not None and r.user_id == user.id and r.state != "deleted" else None


async def due_radars(s: AsyncSession, now: datetime, limit: int, *, postgres: bool) -> list[Radar]:
    q = (
        select(Radar)
        .where(Radar.state.in_(("active", "error")), Radar.next_check_at <= now)
        .order_by(Radar.next_check_at)
        .limit(limit)
    )
    if postgres:
        # Several workers can run; each claims a disjoint batch.
        q = q.with_for_update(skip_locked=True)
    return list((await s.execute(q)).scalars().all())


async def observations(s: AsyncSession, radar_id: int) -> dict[str, RadarObservation]:
    rows = (await s.execute(select(RadarObservation).where(RadarObservation.radar_id == radar_id))).scalars().all()
    return {r.result_key: r for r in rows}


async def add_notification(s: AsyncSession, n: Notification) -> bool:
    exists = (await s.execute(select(Notification.id).where(Notification.dedupe_key == n.dedupe_key))).first()
    if exists:
        return False
    s.add(n)
    await s.flush()
    return True


async def pending_notifications(s: AsyncSession, now: datetime, limit: int = 200) -> list[Notification]:
    return list(
        (
            await s.execute(
                select(Notification)
                .where(Notification.sent_at.is_(None), Notification.deliver_after <= now, Notification.attempts < 5)
                .order_by(Notification.user_id, Notification.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def audit(s: AsyncSession, user_id: int | None, action: str, **detail: Any) -> None:
    s.add(AuditLog(user_id=user_id, action=action, detail=detail))


async def forget_user(s: AsyncSession, user: User) -> None:
    """GDPR-style erasure: everything tied to the user goes, audit rows are anonymised."""
    await s.execute(update(AuditLog).where(AuditLog.user_id == user.id).values(user_id=None))
    for model in (Notification, SearchSession, Booking, Itinerary):
        await s.execute(delete(model).where(model.user_id == user.id))  # type: ignore[attr-defined]
    radar_ids = (await s.execute(select(Radar.id).where(Radar.user_id == user.id))).scalars().all()
    if radar_ids:
        await s.execute(delete(RadarObservation).where(RadarObservation.radar_id.in_(radar_ids)))
        await s.execute(delete(Radar).where(Radar.id.in_(radar_ids)))
    await s.delete(user)
