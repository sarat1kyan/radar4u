"""Where a fact came from and how much it can be trusted.

The rules this module encodes:
  * every result carries a Provenance (provider, retrieval time, freshness);
  * a boolean-ish attribute is a Tri (yes / no / unknown), never a bare bool,
    so a missing amenity cannot silently turn into a confirmed one;
  * freshness distinguishes live bookable offers from cached estimates,
    timetables and static directory data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Freshness(StrEnum):
    # Fetched just now from a source that can sell or reserve it.
    LIVE = "live"
    # Live data reused from our own short-lived cache.
    RECENT = "recent"
    # Aggregated or historical price, not a purchasable offer.
    CACHED_ESTIMATE = "cached_estimate"
    # A schedule. Says a service runs, nothing about seats or tickets.
    TIMETABLE = "timetable"
    # Directory data (name, address, opening hours) maintained by a community or merchant.
    DIRECTORY = "directory"
    # Development fixture. Never shown without a visible label.
    FIXTURE = "fixture"


class Tri(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

    @classmethod
    def from_osm(cls, value: str | None) -> Tri:
        if value is None:
            return cls.UNKNOWN
        v = value.strip().lower()
        if v in {"yes", "only", "designated", "limited", "true", "1", "free", "customers"}:
            return cls.YES
        if v in {"no", "false", "0", "none"}:
            return cls.NO
        return cls.UNKNOWN


class Provenance(BaseModel):
    provider: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    freshness: Freshness
    source_url: str | None = None
    attribution: str | None = None
    # Provider-native identifier, used for revalidation and dedup.
    external_id: str | None = None

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or utcnow()) - self.retrieved_at

    def is_stale(self, max_age: timedelta, now: datetime | None = None) -> bool:
        return self.age(now) > max_age


# How long a given freshness class may be presented as current before we demote it.
FRESHNESS_TTL: dict[Freshness, timedelta] = {
    Freshness.LIVE: timedelta(minutes=15),
    Freshness.RECENT: timedelta(hours=1),
    Freshness.CACHED_ESTIMATE: timedelta(days=2),
    Freshness.TIMETABLE: timedelta(days=1),
    Freshness.DIRECTORY: timedelta(days=30),
    Freshness.FIXTURE: timedelta(days=36500),
}
