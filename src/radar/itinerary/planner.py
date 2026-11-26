"""Cross-domain itineraries.

An itinerary is a list of timed items (stay, dining, activity, transport),
each marked confirmed (booked), proposed (a real venue we suggest) or
estimate (time or price we inferred). The validator catches overlaps and
connections that are too tight for the distance between places; replacing an
item shifts the unlocked items that depend on it instead of rebuilding the plan.

The builder fills template slots with real search results. It never invents
venues: a slot without a suitable result is left empty with a reason.
"""

from __future__ import annotations

import itertools
import math
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from radar.core.money import Money
from radar.core.provenance import Tri
from radar.core.timeutil import now_in, tz
from radar.domain.common import DateWindow, Domain, GeoPoint, LocationRef, Party, StayDates
from radar.domain.intents import PlanKind, SearchIntent
from radar.providers import osm_hours
from radar.providers.base import SearchContext
from radar.providers.weather import WeatherService
from radar.search.filters import Tier
from radar.search.pipeline import SearchPipeline

WALK_KMH = 4.5
CITY_TRANSIT_KMH = 18.0
BUFFER_MIN = 10


class ItemStatus(StrEnum):
    CONFIRMED = "confirmed"
    PROPOSED = "proposed"
    ESTIMATE = "estimate"
    EMPTY = "empty"


class ItemKind(StrEnum):
    STAY = "stay"
    DINING = "dining"
    ACTIVITY = "activity"
    TRANSPORT = "transport"


class Item(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: ItemKind
    title: str
    start: datetime
    end: datetime
    status: ItemStatus = ItemStatus.PROPOSED
    point: GeoPoint | None = None
    price: Money | None = None
    result_id: str | None = None
    provider: str | None = None
    depends_on: str | None = None
    locked: bool = False
    outdoor: bool = False
    opening_hours: str | None = None
    note: str | None = None


class Warning_(BaseModel):
    code: str
    items: list[str]
    params: dict[str, Any] = Field(default_factory=dict)


class Itinerary(BaseModel):
    title: str
    kind: PlanKind | None = None
    location: LocationRef | None = None
    party: Party = Field(default_factory=Party)
    budget: Money | None = None
    items: list[Item] = Field(default_factory=list)
    warnings: list[Warning_] = Field(default_factory=list)
    weather: dict[str, str] = Field(default_factory=dict)

    def item(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def budget_status(self) -> tuple[Money | None, int]:
        known = [i.price for i in self.items if i.price is not None and i.status is not ItemStatus.EMPTY]
        unknown = sum(
            1
            for i in self.items
            if i.price is None and i.status is not ItemStatus.EMPTY and i.kind is not ItemKind.TRANSPORT
        )
        if not known:
            return None, unknown
        cur = known[0].currency
        same = [m for m in known if m.currency == cur]
        total = Money.of(sum((m.amount for m in same), Decimal(0)), cur)
        return total, unknown + (len(known) - len(same))


def travel_minutes(a: GeoPoint, b: GeoPoint) -> int:
    """Rough door-to-door estimate: walk if close, otherwise city transit, plus a buffer.
    Labeled an estimate everywhere it is shown."""
    km = a.distance_m(b) / 1000 * 1.3  # street detour factor
    speed = WALK_KMH if km <= 1.5 else CITY_TRANSIT_KMH
    return math.ceil(km / speed * 60) + BUFFER_MIN


def validate(it: Itinerary) -> list[Warning_]:
    warnings: list[Warning_] = []
    timed = sorted(
        (i for i in it.items if i.status is not ItemStatus.EMPTY and i.kind is not ItemKind.STAY), key=lambda i: i.start
    )
    for a, b in itertools.pairwise(timed):
        if b.start < a.end:
            warnings.append(Warning_(code="overlap", items=[a.id, b.id], params={"a": a.title, "b": b.title}))
        elif a.point and b.point:
            need = travel_minutes(a.point, b.point)
            have = int((b.start - a.end).total_seconds() // 60)
            if have < need:
                warnings.append(
                    Warning_(
                        code="tight",
                        items=[a.id, b.id],
                        params={"a": a.title, "b": b.title, "minutes": have, "needed": need},
                    )
                )
    for i in timed:
        if i.opening_hours:
            minutes = int((i.end - i.start).total_seconds() // 60)
            if osm_hours.open_for(i.opening_hours, i.start, minutes) is Tri.NO:
                warnings.append(Warning_(code="closed", items=[i.id], params={"a": i.title}))
        if i.outdoor and it.weather.get(i.start.date().isoformat()) in ("rain", "snow", "storm"):
            warnings.append(
                Warning_(
                    code="weather",
                    items=[i.id],
                    params={"a": i.title, "weather": it.weather[i.start.date().isoformat()]},
                )
            )
    if it.budget is not None:
        total, _ = it.budget_status()
        if total is not None and total.currency == it.budget.currency and total.amount > it.budget.amount:
            warnings.append(Warning_(code="over_budget", items=[], params={"total": str(total)}))
    it.warnings = warnings
    return warnings


def replace(it: Itinerary, item_id: str, new: Item) -> Itinerary:
    """Swap one item; shift unlocked dependents by the change in end time, then re-validate."""
    old = it.item(item_id)
    if old is None:
        raise KeyError(item_id)
    new.id = old.id
    new.depends_on = old.depends_on
    delta = new.end - old.end
    it.items = [new if i.id == item_id else i for i in it.items]
    if delta:
        shifted = {item_id}
        changed = True
        while changed:
            changed = False
            for i in it.items:
                if i.depends_on in shifted and i.id not in shifted and not i.locked:
                    i.start += delta
                    i.end += delta
                    shifted.add(i.id)
                    changed = True
    validate(it)
    return it


# (day offset, kind, domain, categories, start, minutes, outdoor)
Slot = tuple[int, ItemKind, Domain, list[str], time, int, bool]

TEMPLATES: dict[PlanKind, list[Slot]] = {
    PlanKind.ROMANTIC_WEEKEND: [
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:museum"], time(15, 0), 120, False),
        (0, ItemKind.DINING, Domain.DINING, [], time(19, 30), 120, False),
        (1, ItemKind.ACTIVITY, Domain.EVENT, ["event:exhibition"], time(11, 0), 120, False),
        (1, ItemKind.DINING, Domain.DINING, ["place:cafe"], time(13, 30), 75, False),
        (1, ItemKind.ACTIVITY, Domain.EVENT, ["event:theatre"], time(16, 0), 120, False),
    ],
    PlanKind.EVENING_DATE: [
        (0, ItemKind.DINING, Domain.DINING, [], time(19, 30), 120, False),
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:theatre"], time(21, 45), 90, False),
    ],
    PlanKind.DAY_TRIP: [
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:museum"], time(10, 0), 120, False),
        (0, ItemKind.DINING, Domain.DINING, [], time(13, 0), 75, False),
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:exhibition"], time(15, 0), 120, False),
    ],
    PlanKind.CITY_TRIP: [
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:museum"], time(10, 30), 150, False),
        (0, ItemKind.DINING, Domain.DINING, [], time(13, 30), 75, False),
        (0, ItemKind.DINING, Domain.DINING, [], time(19, 30), 105, False),
        (1, ItemKind.ACTIVITY, Domain.EVENT, ["event:exhibition"], time(10, 30), 120, False),
        (1, ItemKind.DINING, Domain.DINING, ["place:cafe"], time(13, 0), 60, False),
        (1, ItemKind.DINING, Domain.DINING, [], time(19, 30), 105, False),
        (2, ItemKind.ACTIVITY, Domain.EVENT, ["event:museum"], time(11, 0), 120, False),
    ],
    PlanKind.BIRTHDAY: [
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:escape_room"], time(17, 0), 90, False),
        (0, ItemKind.DINING, Domain.DINING, [], time(19, 30), 150, False),
    ],
    PlanKind.LAYOVER: [
        (0, ItemKind.DINING, Domain.DINING, [], time(12, 0), 60, False),
    ],
    PlanKind.FAMILY_HOLIDAY: [
        (0, ItemKind.ACTIVITY, Domain.EVENT, ["event:amusement_park"], time(10, 0), 240, True),
        (0, ItemKind.DINING, Domain.DINING, [], time(18, 30), 90, False),
    ],
    PlanKind.BUSINESS_TRIP: [
        (0, ItemKind.DINING, Domain.DINING, [], time(20, 0), 90, False),
    ],
}


class Planner:
    def __init__(self, pipeline: SearchPipeline, weather: WeatherService | None = None) -> None:
        self.pipeline = pipeline
        self.weather = weather

    async def build(self, intent: SearchIntent, ctx: SearchContext) -> Itinerary:
        kind = intent.plan_kind or PlanKind.CITY_TRIP
        loc = intent.location or intent.destination
        if loc is None or loc.place is None:
            raise ValueError("plan needs a resolved location")
        z = loc.place.timezone
        start_day: date = intent.when.start.date() if intent.when and intent.when.start else now_in(z).date()
        it = Itinerary(title=loc.place.name, kind=kind, location=loc, party=intent.party, budget=intent.budget.max)
        days = max(1, max(s[0] for s in TEMPLATES[kind]) + 1)

        if self.weather is not None:
            for f in await self.weather.daily(loc.place.point, start_day, start_day + timedelta(days=days - 1)):
                it.weather[f.day.isoformat()] = f.condition

        if days > 1 or kind in (PlanKind.ROMANTIC_WEEKEND, PlanKind.CITY_TRIP, PlanKind.FAMILY_HOLIDAY):
            it.items.append(await self._stay(intent, loc, start_day, days, ctx))

        used: set[str] = set()
        prev_id: str | None = None
        for day, ikind, domain, cats, at, minutes, outdoor in TEMPLATES[kind]:
            d = start_day + timedelta(days=day)
            start = datetime.combine(d, at, tz(z))
            sub = SearchIntent(
                domain=domain,
                raw_text=intent.raw_text,
                language=intent.language,
                categories=cats
                + [c for c in intent.categories if c.startswith("cuisine:") and domain is Domain.DINING],
                location=loc,
                party=intent.party,
                when=DateWindow(start=start, end=start),
            )
            for p in intent.preferences:
                if domain in p.definition.domains:
                    sub.set_preference(p.key, p.value)
            item = await self._fill(sub, ikind, start, minutes, outdoor, used, ctx)
            item.depends_on = prev_id if prev_id and day == _day_of(it, prev_id, start_day) else None
            it.items.append(item)
            prev_id = item.id
        validate(it)
        return it

    async def _fill(
        self,
        sub: SearchIntent,
        kind: ItemKind,
        start: datetime,
        minutes: int,
        outdoor: bool,
        used: set[str],
        ctx: SearchContext,
    ) -> Item:
        end = start + timedelta(minutes=minutes)
        try:
            outcome = await self.pipeline.run(
                sub, SearchContext(locale=ctx.locale, currency=ctx.currency, max_results=15)
            )
        except Exception:
            outcome = None
        if outcome is not None:
            for r in outcome.ranked:
                if r.result.id in used or r.tier is Tier.ALTERNATIVE:
                    continue
                used.add(r.result.id)
                res = r.result
                return Item(
                    kind=kind,
                    title=res.title,
                    start=start,
                    end=end,
                    status=ItemStatus.PROPOSED,
                    point=res.point,
                    price=res.price.amount if res.price else None,
                    result_id=res.id,
                    provider=res.provenance.provider,
                    outdoor=outdoor,
                    opening_hours=res.facts.get("opening_hours")
                    if isinstance(res.facts.get("opening_hours"), str)
                    else None,
                    note="unverified" if r.tier is Tier.UNVERIFIED else None,
                )
        reason = "no_coverage" if outcome is None or outcome.no_coverage else "no_match"
        return Item(kind=kind, title="", start=start, end=end, status=ItemStatus.EMPTY, note=reason)

    async def _stay(
        self, intent: SearchIntent, loc: LocationRef, start_day: date, days: int, ctx: SearchContext
    ) -> Item:
        z = tz(loc.place.timezone if loc.place else None)
        stay_intent = SearchIntent(
            domain=Domain.STAY,
            raw_text=intent.raw_text,
            location=loc,
            party=intent.party,
            stay=StayDates(check_in=start_day, check_out=start_day + timedelta(days=days)),
        )
        start = datetime.combine(start_day, time(14, 0), z)
        end = datetime.combine(start_day + timedelta(days=days), time(11, 0), z)
        item = await self._fill(
            stay_intent, ItemKind.STAY, start, int((end - start).total_seconds() // 60), False, set(), ctx
        )
        # Check-in/out times are typical values, not the property's policy.
        if item.status is ItemStatus.PROPOSED:
            item.note = (item.note + ";" if item.note else "") + "times_estimated"
        return item


def _day_of(it: Itinerary, item_id: str, start_day: date) -> int:
    i = it.item(item_id)
    return (i.start.date() - start_day).days if i else -1
