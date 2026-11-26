"""Live opening status for venues ("Open until 23:00", "Closed, opens Mon 12:00")."""

from __future__ import annotations

from datetime import datetime

from radar.core.provenance import Tri
from radar.core.timeutil import now_in
from radar.domain.intents import SearchIntent
from radar.domain.results import Result
from radar.providers import osm_hours


def reference_time(intent: SearchIntent) -> datetime:
    """The moment the user cares about: their requested time, else now at the searched place."""
    if intent.when and intent.when.start and (intent.when.start.hour, intent.when.start.minute) != (0, 0):
        return intent.when.start
    ref = intent.location or intent.destination
    zone = ref.place.timezone if ref and ref.place else None
    return now_in(zone)


def open_status(r: Result, at: datetime) -> tuple[str | None, datetime | None]:
    """('open' | 'closed' | '24_7' | None, time of the next change)."""
    hours = r.facts.get("opening_hours")
    if not isinstance(hours, str):
        return None, None
    if hours.strip() == "24/7":
        return "24_7", None
    state, change = osm_hours.next_change(hours, at)
    if state is Tri.YES:
        return "open", change
    if state is Tri.NO:
        return "closed", change
    return None, None
