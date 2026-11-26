from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def now_in(zone: str | None) -> datetime:
    return datetime.now(tz(zone))


def local_dt(d: date, t: time, zone: str | None) -> datetime:
    return datetime.combine(d, t, tzinfo=tz(zone))


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; attach a timezone first")
    return dt.astimezone(UTC)


def in_quiet_hours(now_local: time, start: time | None, end: time | None) -> bool:
    """Quiet window may wrap midnight (22:00-08:00)."""
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now_local < end
    return now_local >= start or now_local < end


def next_after_quiet(now_local: datetime, start: time | None, end: time | None) -> datetime:
    if not in_quiet_hours(now_local.timetz().replace(tzinfo=None), start, end) or end is None:
        return now_local
    candidate = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate
