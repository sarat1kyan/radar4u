from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from radar.core.money import Money


class Domain(StrEnum):
    DINING = "dining"
    STAY = "stay"
    REAL_ESTATE = "realestate"
    FLIGHT = "flight"
    RAIL = "rail"
    GROUND = "ground"
    EVENT = "event"
    WELLNESS = "wellness"
    SHOPPING = "shopping"
    ESSENTIALS = "essentials"
    PLAN = "plan"

    @property
    def is_transport(self) -> bool:
        return self in {Domain.FLIGHT, Domain.RAIL, Domain.GROUND}

    @property
    def is_venue(self) -> bool:
        """Places you walk into: opening hours and distance matter most."""
        return self in {Domain.DINING, Domain.WELLNESS, Domain.EVENT, Domain.SHOPPING, Domain.ESSENTIALS}


class GeoPoint(BaseModel):
    lat: float
    lon: float

    @field_validator("lat")
    @classmethod
    def _lat(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude out of range")
        return v

    @field_validator("lon")
    @classmethod
    def _lon(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude out of range")
        return v

    def distance_m(self, other: GeoPoint) -> float:
        """Great-circle distance (haversine), metres."""
        r = 6_371_008.8
        p1, p2 = math.radians(self.lat), math.radians(other.lat)
        dp = p2 - p1
        dl = math.radians(other.lon - self.lon)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))


class PlaceKind(StrEnum):
    COUNTRY = "country"
    REGION = "region"
    CITY = "city"
    DISTRICT = "district"
    STATION = "station"
    AIRPORT = "airport"
    POI = "poi"
    ADDRESS = "address"
    COORDINATES = "coordinates"


class Place(BaseModel):
    """A resolved geographic entity with a canonical id."""

    canonical_id: str  # e.g. "osm:R123", "iata:EVN", "geo:40.18,44.51"
    name: str
    display_name: str | None = None
    kind: PlaceKind = PlaceKind.CITY
    country_code: str | None = None  # ISO 3166-1 alpha-2, upper case
    point: GeoPoint
    bbox: tuple[float, float, float, float] | None = None  # south, west, north, east
    timezone: str | None = None  # IANA
    iata: str | None = None
    source: str = "unknown"


class LocationRef(BaseModel):
    """What the user said plus what we resolved it to."""

    text: str
    place: Place | None = None
    radius_m: int | None = None
    alternatives: list[Place] = Field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.place is not None


class Party(BaseModel):
    adults: int = Field(1, ge=1, le=30)
    children_ages: list[int] = Field(default_factory=list)
    rooms: int = Field(1, ge=1, le=10)

    @property
    def size(self) -> int:
        return self.adults + len(self.children_ages)


class BudgetUnit(StrEnum):
    TOTAL = "total"
    PERSON = "person"
    NIGHT = "night"
    MONTH = "month"


class Budget(BaseModel):
    max: Money | None = None
    min: Money | None = None
    unit: BudgetUnit = BudgetUnit.TOTAL


class DateWindow(BaseModel):
    """A point or window in local time of the search location (tz-aware)."""

    start: datetime | None = None
    end: datetime | None = None
    flexible_minutes: int = 0

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetimes must be timezone-aware")
        return v


class StayDates(BaseModel):
    check_in: date
    check_out: date

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


class SortMode(StrEnum):
    BEST = "best"
    CHEAPEST = "cheapest"
    RATED = "rated"
    CLOSEST = "closest"
    FASTEST = "fastest"
    EARLIEST = "earliest"
    FLEXIBLE = "flexible"
    VALUE = "value"
