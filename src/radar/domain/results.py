"""Normalized result model shared by all domains.

Provider adapters map their payloads into Result. Domain-specific details that
do not deserve a first-class field go into `facts` (plain JSON values only).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from radar.core.money import Money
from radar.core.provenance import Provenance, Tri
from radar.domain.common import BudgetUnit, Domain, GeoPoint


class Availability(StrEnum):
    # The provider told us this can be booked / bought for the given parameters.
    CONFIRMED = "confirmed"
    # Can be requested; the provider will confirm later.
    ON_REQUEST = "on_request"
    NOT_AVAILABLE = "not_available"
    # We have no availability data at all (directory listings, timetables).
    UNKNOWN = "unknown"


class PriceKind(StrEnum):
    OFFER = "offer"  # purchasable at retrieval time
    FROM = "from"  # lowest advertised, conditions may differ
    ESTIMATE = "estimate"  # cached/historical, not purchasable


class Price(BaseModel):
    amount: Money
    unit: BudgetUnit = BudgetUnit.TOTAL
    kind: PriceKind = PriceKind.OFFER
    includes_taxes: Tri = Tri.UNKNOWN
    payable_at_property: Money | None = None
    unknown_fees: bool = True
    conditions: list[str] = Field(default_factory=list)


class Rating(BaseModel):
    value: float
    scale: float = 5.0
    count: int | None = None
    source: str

    @property
    def normalized(self) -> float:
        """0..1, not comparable across sources without the count and source."""
        return max(0.0, min(1.0, self.value / self.scale)) if self.scale else 0.0


class BookingMode(StrEnum):
    DIRECT = "direct"  # we can place the booking through an authorized provider
    HANDOFF = "handoff"  # official page where the user completes it
    CONTACT = "contact"  # phone / e-mail only
    NONE = "none"


class LinkKind(StrEnum):
    WEBSITE = "website"
    BOOKING = "booking"
    MENU = "menu"
    MAP = "map"
    PHONE = "phone"
    SOURCE = "source"


class Link(BaseModel):
    kind: LinkKind
    url: str
    label: str | None = None


class Stop(BaseModel):
    name: str
    code: str | None = None  # IATA / station id
    time: datetime | None = None  # tz-aware, local to the stop
    platform: str | None = None
    point: GeoPoint | None = None


class Leg(BaseModel):
    mode: str  # flight, rail, highspeed_rail, bus, coach, ferry, walk, tram, subway ...
    carrier: str | None = None
    service: str | None = None  # flight / train number or line name
    origin: Stop
    destination: Stop
    duration_min: int | None = None
    intermediate_stops: int | None = None
    realtime: bool = False
    cancelled: bool = False


class Connection(BaseModel):
    at: str  # stop name
    minutes: int | None = None
    self_transfer: Tri = Tri.UNKNOWN
    station_change: bool = False
    overnight: bool = False


class Result(BaseModel):
    id: str = ""
    domain: Domain
    title: str
    subtitle: str | None = None
    categories: list[str] = Field(default_factory=list)

    point: GeoPoint | None = None
    address: str | None = None
    distance_m: float | None = None

    price: Price | None = None
    rating: Rating | None = None
    attributes: dict[str, Tri] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)

    availability: Availability = Availability.UNKNOWN
    booking: BookingMode = BookingMode.NONE
    links: list[Link] = Field(default_factory=list)

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    legs: list[Leg] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)

    provenance: Provenance

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            basis = f"{self.provenance.provider}:{self.provenance.external_id or self.title}"
            self.id = hashlib.sha1(basis.encode(), usedforsecurity=False).hexdigest()[:16]

    def attr(self, key: str) -> Tri:
        return self.attributes.get(key, Tri.UNKNOWN)

    @property
    def duration_min(self) -> int | None:
        if self.starts_at and self.ends_at:
            return int((self.ends_at - self.starts_at).total_seconds() // 60)
        return None

    @property
    def transfers(self) -> int | None:
        if not self.legs:
            return None
        return max(0, len([leg for leg in self.legs if leg.mode != "walk"]) - 1)

    def link(self, kind: LinkKind) -> Link | None:
        return next((lk for lk in self.links if lk.kind == kind), None)
