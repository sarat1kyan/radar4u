"""Structured search intention.

This is the contract between language understanding and everything else. The
rule parser and LLM backends both produce a SearchIntent; the pipeline only
ever consumes one. Hard constraints and soft preferences are separate lists so
that relaxing a requirement is always an explicit, visible operation.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from radar.core.money import Money
from radar.domain.common import (
    Budget,
    DateWindow,
    Domain,
    LocationRef,
    Party,
    SortMode,
    StayDates,
)
from radar.domain.vocabulary import CONSTRAINTS, ValueType


class Constraint(BaseModel):
    key: str
    value: bool | int | float | str = True

    @field_validator("key")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in CONSTRAINTS:
            raise ValueError(f"unknown constraint key: {v}")
        return v

    @property
    def definition(self) -> Any:
        return CONSTRAINTS[self.key]

    @property
    def value_type(self) -> ValueType:
        return CONSTRAINTS[self.key].value_type


class MonitorKind(StrEnum):
    PRICE_BELOW = "price_below"
    AVAILABILITY = "availability"
    NEW_RESULTS = "new_results"


class MonitorSpec(BaseModel):
    kind: MonitorKind
    threshold: Money | None = None


class PlanKind(StrEnum):
    ROMANTIC_WEEKEND = "romantic_weekend"
    CITY_TRIP = "city_trip"
    BUSINESS_TRIP = "business_trip"
    FAMILY_HOLIDAY = "family_holiday"
    LAYOVER = "layover"
    BIRTHDAY = "birthday"
    EVENING_DATE = "evening_date"
    DAY_TRIP = "day_trip"


class SearchIntent(BaseModel):
    domain: Domain
    raw_text: str = ""
    language: str = "en"

    # Free-form category tokens from a controlled per-domain list
    # (cuisine:sushi, place:spa, event:concert, property:apartment ...).
    categories: list[str] = Field(default_factory=list)

    location: LocationRef | None = None
    origin: LocationRef | None = None
    destination: LocationRef | None = None

    when: DateWindow | None = None
    stay: StayDates | None = None
    # For transport: user wants to arrive before the window end rather than depart in it.
    arrive_by: bool = False

    party: Party = Field(default_factory=Party)
    budget: Budget = Field(default_factory=Budget)

    requirements: list[Constraint] = Field(default_factory=list)
    preferences: list[Constraint] = Field(default_factory=list)

    sort: SortMode = SortMode.BEST
    monitor: MonitorSpec | None = None
    plan_kind: PlanKind | None = None

    # Filled by validation: fields that block execution, in priority order.
    missing: list[str] = Field(default_factory=list)
    # Things the parser saw but could not map; shown to the user, never dropped silently.
    unparsed: list[str] = Field(default_factory=list)

    def requirement(self, key: str) -> Constraint | None:
        return next((c for c in self.requirements if c.key == key), None)

    def has_requirement(self, key: str) -> bool:
        return self.requirement(key) is not None

    def set_requirement(self, key: str, value: bool | int | float | str = True) -> None:
        self.preferences = [c for c in self.preferences if c.key != key]
        self.requirements = [c for c in self.requirements if c.key != key]
        self.requirements.append(Constraint(key=key, value=value))

    def set_preference(self, key: str, value: bool | int | float | str = True) -> None:
        if self.has_requirement(key):
            return
        self.preferences = [c for c in self.preferences if c.key != key]
        self.preferences.append(Constraint(key=key, value=value))

    def drop(self, key: str) -> None:
        self.requirements = [c for c in self.requirements if c.key != key]
        self.preferences = [c for c in self.preferences if c.key != key]

    @property
    def search_date(self) -> date | None:
        if self.stay:
            return self.stay.check_in
        if self.when and self.when.start:
            return self.when.start.date()
        return None

    def primary_location(self) -> LocationRef | None:
        if self.domain.is_transport:
            return self.origin
        return self.location or self.destination
