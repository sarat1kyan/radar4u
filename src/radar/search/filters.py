"""Strict constraint evaluation.

Each requirement evaluates to met / unmet / unverified. A result is an exact
match only when every requirement is met; anything unverified puts it in a
separate tier, anything unmet makes it an alternative. Requirements are
never relaxed here; relaxing is a user decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import IntEnum

from radar.core.money import Money
from radar.core.provenance import Tri
from radar.domain.common import BudgetUnit, Domain
from radar.domain.intents import Constraint, SearchIntent
from radar.domain.results import Availability, Result
from radar.domain.vocabulary import ValueType
from radar.providers import osm_hours


class Tier(IntEnum):
    EXACT = 0
    UNVERIFIED = 1
    ALTERNATIVE = 2


@dataclass
class MatchReport:
    met: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    pref_met: list[str] = field(default_factory=list)
    pref_unmet: list[str] = field(default_factory=list)
    pref_unknown: list[str] = field(default_factory=list)
    price_in_budget_ccy: Money | None = None

    @property
    def tier(self) -> Tier:
        if self.unmet:
            return Tier.ALTERNATIVE
        if self.unverified:
            return Tier.UNVERIFIED
        return Tier.EXACT

    def add(self, key: str, t: Tri, *, preference: bool = False) -> None:
        if preference:
            {Tri.YES: self.pref_met, Tri.NO: self.pref_unmet, Tri.UNKNOWN: self.pref_unknown}[t].append(key)
        else:
            {Tri.YES: self.met, Tri.NO: self.unmet, Tri.UNKNOWN: self.unverified}[t].append(key)


def _rating_tri(result: Result, wanted: float) -> Tri:
    if result.rating is None:
        return Tri.UNKNOWN
    scale = 5.0 if wanted <= 5 else 10.0
    have = result.rating.normalized * scale
    return Tri.YES if have >= wanted else Tri.NO


def _hhmm(v: object) -> time | None:
    try:
        hh, mm = str(v).split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def constraint_tri(result: Result, c: Constraint, intent: SearchIntent) -> Tri:
    key, vt = c.key, c.value_type
    if key == "rating.min":
        return _rating_tri(result, float(c.value))
    if key == "direct" and result.legs:
        return Tri.YES if result.transfers == 0 else Tri.NO
    if key == "max_transfers":
        return Tri.UNKNOWN if result.transfers is None else Tri.YES if result.transfers <= int(c.value) else Tri.NO
    if key == "max_duration_min":
        d = result.duration_min
        return Tri.UNKNOWN if d is None else Tri.YES if d <= int(c.value) else Tri.NO
    if key == "cabin":
        cabin = result.facts.get("cabin")
        return Tri.UNKNOWN if cabin is None else Tri.YES if str(cabin) == str(c.value) else Tri.NO
    if key in ("arrive_before", "depart_after"):
        t = _hhmm(c.value)
        dt = result.ends_at if key == "arrive_before" else result.starts_at
        if t is None or dt is None:
            return Tri.UNKNOWN
        ok = dt.time() <= t if key == "arrive_before" else dt.time() >= t
        return Tri.YES if ok else Tri.NO
    if key == "open_at":
        return _open_tri(result, intent)
    if vt in (ValueType.INT_MIN, ValueType.NUM_MIN) and key in result.attributes:
        return result.attributes[key]
    return result.attr(key)


def _open_tri(result: Result, intent: SearchIntent) -> Tri:
    if intent.when is None or intent.when.start is None:
        return Tri.UNKNOWN
    hours = result.facts.get("opening_hours")
    minutes = 90 if intent.domain is Domain.DINING else 60
    return osm_hours.open_for(hours if isinstance(hours, str) else None, intent.when.start, minutes)


def _has_clock_time(dt: datetime | None) -> bool:
    return dt is not None and (dt.hour, dt.minute) != (0, 0)


def time_window_tri(result: Result, intent: SearchIntent) -> Tri | None:
    """None when the time window is not applicable to this result."""
    w = intent.when
    if w is None or w.start is None:
        return None
    if intent.domain.is_transport or intent.domain is Domain.EVENT:
        if intent.arrive_by and w.end is not None:
            if result.ends_at is None:
                return Tri.UNKNOWN
            return Tri.YES if result.ends_at <= w.end and result.ends_at.date() == w.end.date() else Tri.NO
        if result.starts_at is None:
            return Tri.UNKNOWN
        end = w.end if w.end and w.end > w.start else None
        if end is None:
            # A point in time: accept departures within +-2h for transport, same evening for events.
            slack = timedelta(hours=2 if intent.domain.is_transport else 6)
            return Tri.YES if abs(result.starts_at - w.start) <= slack else Tri.NO
        return Tri.YES if w.start <= result.starts_at <= end else Tri.NO
    return None


def price_for_budget(result: Result, intent: SearchIntent, converted: Money | None) -> Money | None:
    """Result price expressed in the budget's unit and currency, or None if unknown."""
    if result.price is None or intent.budget.max is None:
        return None
    price = converted or result.price.amount
    if price.currency != intent.budget.max.currency:
        return None
    unit, have = intent.budget.unit, result.price.unit
    amount = price.amount
    if unit == have:
        pass
    elif unit is BudgetUnit.NIGHT and have is BudgetUnit.TOTAL:
        nights = intent.stay.nights if intent.stay else result.facts.get("nights")
        if not nights:
            return None
        amount = amount / Decimal(int(nights))
    elif unit is BudgetUnit.TOTAL and have is BudgetUnit.NIGHT:
        if not intent.stay:
            return None
        amount = amount * intent.stay.nights
    elif unit is BudgetUnit.PERSON and have is BudgetUnit.TOTAL:
        amount = amount / Decimal(max(1, intent.party.size))
    elif unit is BudgetUnit.TOTAL and have is BudgetUnit.PERSON:
        amount = amount * intent.party.size
    else:
        return None
    if result.price.payable_at_property is not None and result.price.payable_at_property.currency == price.currency:
        amount += result.price.payable_at_property.amount
    return Money(amount=amount, currency=price.currency)


def evaluate(result: Result, intent: SearchIntent, converted: Money | None = None) -> MatchReport:
    rep = MatchReport()
    for c in intent.requirements:
        rep.add(c.key, constraint_tri(result, c, intent))
    for c in intent.preferences:
        if c.definition.preference_only:
            continue  # subjective; used by ranking only through categories/text, never verified
        rep.add(c.key, constraint_tri(result, c, intent), preference=True)

    if (
        intent.domain in (Domain.DINING, Domain.WELLNESS)
        and intent.when
        and _has_clock_time(intent.when.start)
        and not intent.has_requirement("open_at")
    ):
        rep.add("open_at", _open_tri(result, intent))

    if (tw := time_window_tri(result, intent)) is not None:
        rep.add("time_window", tw)

    if intent.budget.max is not None:
        p = price_for_budget(result, intent, converted)
        rep.price_in_budget_ccy = p
        if p is None:
            rep.add("budget", Tri.UNKNOWN)
        else:
            within = p.amount <= intent.budget.max.amount
            if intent.budget.min is not None and p.amount < intent.budget.min.amount:
                within = False
            rep.add("budget", Tri.YES if within else Tri.NO)

    if result.availability is Availability.NOT_AVAILABLE:
        rep.add("availability", Tri.NO)

    for cat in intent.categories:
        if cat.startswith("cuisine:") and intent.domain is Domain.DINING and result.provenance.provider != "osm":
            cuisines = [c.split(":", 1)[1] for c in result.categories if c.startswith("cuisine:")]
            if cuisines and cat.split(":", 1)[1] not in cuisines:
                rep.add("cuisine", Tri.NO)
    return rep
