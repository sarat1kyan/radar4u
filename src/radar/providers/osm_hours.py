"""Evaluator for the common subset of the OSM opening_hours syntax.

Supported: "24/7", weekday ranges and lists (Mo-Fr, Sa,Su), several time spans
per rule, spans that cross midnight (18:00-02:00), "off"/"closed", and later
rules overriding earlier ones for the same day. Anything else (PH, SH, month
ranges, week numbers, sunrise, comments) makes the result UNKNOWN rather than
a guess. Opening hours are never treated as reservation availability.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from radar.core.provenance import Tri

_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_SPAN = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\+?$")
_UNSUPPORTED = re.compile(
    r"\b(PH|SH|easter|sunrise|sunset|dawn|dusk|week|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|"
    r"Nov|Dec)\b|\"|\[|\|\|"
)


class HoursParseError(ValueError):
    pass


def _parse_days(spec: str) -> set[int]:
    days: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in _DAYS or b not in _DAYS:
                raise HoursParseError(part)
            i, j = _DAYS.index(a), _DAYS.index(b)
            rng = range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)]
            days.update(rng)
        elif part in _DAYS:
            days.add(_DAYS.index(part))
        else:
            raise HoursParseError(part)
    return days


def _parse_spans(spec: str) -> list[tuple[int, int]]:
    spans = []
    for part in spec.split(","):
        m = _SPAN.match(part.strip())
        if not m:
            raise HoursParseError(part)
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end <= start:
            end += 24 * 60  # crosses midnight
        spans.append((start, end))
    return spans


def parse(value: str) -> dict[int, list[tuple[int, int]]]:
    """weekday -> [(start_minute, end_minute)], end may exceed 1440."""
    v = value.strip()
    if not v:
        raise HoursParseError("empty")
    if v == "24/7":
        return {d: [(0, 24 * 60)] for d in range(7)}
    if _UNSUPPORTED.search(v):
        raise HoursParseError("unsupported construct")
    table: dict[int, list[tuple[int, int]]] = {}
    for rule in (r.strip() for r in v.split(";")):
        if not rule:
            continue
        m = re.match(r"^((?:[A-Z][a-z](?:-[A-Z][a-z])?)(?:,(?:[A-Z][a-z](?:-[A-Z][a-z])?))*)\s*(.*)$", rule)
        if m:
            days, rest = _parse_days(m.group(1)), m.group(2).strip()
        else:
            days, rest = set(range(7)), rule
        if rest.lower() in ("off", "closed"):
            for d in days:
                table[d] = []
            continue
        spans = _parse_spans(rest)
        for d in days:
            table[d] = spans  # later rules override earlier ones
    return table


def is_open(value: str | None, at: datetime) -> Tri:
    if not value:
        return Tri.UNKNOWN
    try:
        table = parse(value)
    except HoursParseError:
        return Tri.UNKNOWN
    minute = at.hour * 60 + at.minute
    today, yesterday = at.weekday(), (at - timedelta(days=1)).weekday()
    if any(s <= minute < e for s, e in table.get(today, [])):
        return Tri.YES
    if any(e > 1440 and minute < e - 1440 for _, e in table.get(yesterday, [])):
        return Tri.YES
    if today not in table and yesterday not in table:
        return Tri.UNKNOWN
    return Tri.NO


def open_for(value: str | None, at: datetime, minutes: int) -> Tri:
    """Open for the whole stretch [at, at+minutes]."""
    end = at + timedelta(minutes=minutes)
    first, last = is_open(value, at), is_open(value, end - timedelta(minutes=1))
    if Tri.NO in (first, last):
        return Tri.NO
    if first is Tri.YES and last is Tri.YES:
        return Tri.YES
    return Tri.UNKNOWN


def describe_day(value: str | None, day: int) -> str | None:
    try:
        table = parse(value or "")
    except HoursParseError:
        return None
    spans = table.get(day)
    if spans is None:
        return None
    if not spans:
        return "closed"

    def hm(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    return ", ".join(f"{hm(s)}-{hm(e if e <= 1440 else e - 1440)}" for s, e in spans)


def next_change(value: str | None, at: datetime, horizon_days: int = 7) -> tuple[Tri, datetime | None]:
    """(open now?, when that changes). Scans forward minute-accurately over the parsed table."""
    if not value:
        return Tri.UNKNOWN, None
    try:
        table = parse(value)
    except HoursParseError:
        return Tri.UNKNOWN, None
    now_state = is_open(value, at)
    if now_state is Tri.UNKNOWN:
        return Tri.UNKNOWN, None
    # Candidate boundaries: every span start/end over the horizon.
    base = at.replace(hour=0, minute=0, second=0, microsecond=0)
    points: list[datetime] = []
    for d in range(-1, horizon_days + 1):
        day = base + timedelta(days=d)
        for s, e in table.get(day.weekday(), []):
            points.append(day + timedelta(minutes=s))
            points.append(day + timedelta(minutes=e))
    for p in sorted(x for x in points if x > at):
        if is_open(value, p) is not now_state:
            return now_state, p
    if value.strip() == "24/7":
        return Tri.YES, None
    return now_state, None


__all__ = ["HoursParseError", "describe_day", "is_open", "next_change", "open_for", "parse"]
