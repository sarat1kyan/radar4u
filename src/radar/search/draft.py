"""Structured search drafts shared by the bot's button panel and the Mini App.

A draft is a small dict of choices (domain, category, places, day, time,
nights, guests, filters, sort). `build_intent` turns it into a SearchIntent
with the same validation as typed requests.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from radar.core.timeutil import tz
from radar.domain.common import DateWindow, Domain, LocationRef, Party, Place, SortMode, StayDates
from radar.domain.intents import Constraint, PlanKind, SearchIntent
from radar.domain.vocabulary import CONSTRAINTS

GRID: list[Domain] = [
    Domain.DINING,
    Domain.STAY,
    Domain.RAIL,
    Domain.FLIGHT,
    Domain.EVENT,
    Domain.WELLNESS,
    Domain.GROUND,
    Domain.REAL_ESTATE,
    Domain.PLAN,
]

STEPS: dict[Domain, list[str]] = {
    Domain.DINING: ["cat", "city", "day", "time", "pax", "filters"],
    Domain.WELLNESS: ["cat", "city", "day", "time", "filters"],
    Domain.EVENT: ["cat", "city", "day", "filters"],
    Domain.STAY: ["cat", "city", "day", "nights", "pax", "filters"],
    Domain.RAIL: ["origin", "dest", "day", "time", "filters"],
    Domain.GROUND: ["cat", "origin", "dest", "day", "time", "filters"],
    Domain.FLIGHT: ["origin", "dest", "day", "pax", "filters"],
    Domain.REAL_ESTATE: ["cat", "city", "rooms", "filters"],
    Domain.SHOPPING: ["city", "filters"],
    Domain.PLAN: ["cat", "city", "day", "pax", "filters"],
}

CATEGORIES: dict[Domain, list[str]] = {
    Domain.DINING: [
        "any",
        "place.cafe",
        "place.bar",
        "place.pub",
        "place.bakery",
        "place.brunch",
        "cuisine.sushi",
        "cuisine.pizza",
        "cuisine.italian",
        "cuisine.georgian",
        "cuisine.armenian",
        "cuisine.japanese",
        "cuisine.chinese",
        "cuisine.indian",
        "cuisine.burger",
        "cuisine.seafood",
        "cuisine.steak_house",
        "cuisine.barbecue",
    ],
    Domain.WELLNESS: [
        "place.spa",
        "place.massage",
        "place.sauna",
        "place.beauty",
        "place.hairdresser",
        "place.fitness",
        "place.coworking",
        "place.pet_grooming",
    ],
    Domain.EVENT: [
        "any",
        "event.museum",
        "event.exhibition",
        "event.theatre",
        "event.cinema",
        "event.concert",
        "event.escape_room",
        "event.karting",
        "event.amusement_park",
    ],
    Domain.STAY: ["any", "property.hotel", "property.hostel", "property.guesthouse", "property.apartment"],
    Domain.GROUND: ["mode.multimodal", "mode.bus", "mode.ferry"],
    Domain.REAL_ESTATE: ["property.apartment", "property.house", "property.room"],
    Domain.PLAN: ["plan.romantic_weekend", "plan.city_trip", "plan.evening_date", "plan.day_trip", "plan.birthday"],
}

FILTERS: dict[Domain, list[str]] = {
    Domain.DINING: [
        "outdoor_seating",
        "diet.vegetarian",
        "diet.vegan",
        "diet.halal",
        "wheelchair",
        "child_friendly",
        "takeaway",
        "wifi",
    ],
    Domain.WELLNESS: ["wheelchair", "staff.female", "sauna", "pool"],
    Domain.EVENT: ["wheelchair"],
    Domain.STAY: [
        "wifi",
        "breakfast",
        "private_bathroom",
        "free_cancellation",
        "parking",
        "pet_friendly",
        "wheelchair",
        "late_checkin",
    ],
    Domain.RAIL: ["direct", "bicycle", "wheelchair", "first_class"],
    Domain.GROUND: ["direct", "bicycle"],
    Domain.FLIGHT: ["direct", "checked_bag", "refundable"],
    Domain.REAL_ESTATE: ["furnished", "balcony", "pet_friendly", "parking", "elevator"],
    Domain.SHOPPING: ["in_stock", "condition.used"],
    Domain.PLAN: [],
}

SORTS: dict[bool, list[SortMode]] = {
    True: [SortMode.BEST, SortMode.FASTEST, SortMode.EARLIEST],
    False: [SortMode.BEST, SortMode.CHEAPEST, SortMode.CLOSEST, SortMode.RATED],
}


# --- draft -> intent --------------------------------------------------------------------------------


def build_intent(d: dict[str, Any], user: Any) -> SearchIntent:
    dom = Domain(d["domain"])
    intent = SearchIntent(domain=dom, language=user.language, party=Party(adults=int(d.get("pax") or 1)))
    cat = d.get("cat")
    if cat and cat != "any":
        if cat.startswith("plan."):
            intent.plan_kind = PlanKind(cat.split(".", 1)[1])
        else:
            intent.categories.append(cat.replace(".", ":", 1))
    if dom is Domain.GROUND and not any(c.startswith("mode:") for c in intent.categories):
        intent.categories.append("mode:multimodal")
    for key, attr_ in (("city", "location"), ("origin", "origin"), ("dest", "destination")):
        if d.get(key):
            p = Place.model_validate(d[key])
            setattr(intent, attr_, LocationRef(text=p.name, place=p))
    if dom is Domain.PLAN and intent.location is not None:
        intent.destination = intent.location
    ref = intent.primary_location() or intent.location
    zone = (ref.place.timezone if ref and ref.place else None) or user.timezone
    z = tz(zone)
    if d.get("day"):
        day = date.fromisoformat(d["day"])
        if dom is Domain.STAY:
            intent.stay = StayDates(check_in=day, check_out=day + timedelta(days=int(d.get("nights") or 1)))
        else:
            t = time.fromisoformat(d["time"]) if d.get("time") else None
            start = datetime.combine(day, t or time(0, 0), z)
            end = start if t and not dom.is_transport else datetime.combine(day, time(23, 59), z)
            if t and dom.is_transport:
                end = start + timedelta(hours=4)
            intent.when = DateWindow(start=start, end=end)
    if d.get("rooms"):
        intent.set_requirement("bedrooms.min", int(d["rooms"]))
    if dom is Domain.REAL_ESTATE:
        intent.set_requirement("deal.rent")
    for f in d.get("flags", []):
        if f in CONSTRAINTS and dom in CONSTRAINTS[f].domains and not intent.has_requirement(f):
            intent.requirements.append(Constraint(key=f))
    intent.sort = SortMode(d.get("sort", "best"))
    if d.get("open_now") and intent.location is not None:
        now = datetime.now(z).replace(second=0, microsecond=0)
        intent.when = DateWindow(start=now, end=now)
        intent.set_requirement("open_at")
        intent.sort = SortMode.CLOSEST
        if intent.location.radius_m is None:
            intent.location.radius_m = 1500
    return intent


def draft_from_intent(intent: SearchIntent) -> dict[str, Any]:
    """Inverse of build_intent, so any search (typed or tapped) can be reopened in the panel."""
    dom = intent.domain if intent.domain in STEPS else Domain.DINING
    d: dict[str, Any] = {"domain": dom.value, "flags": [], "sort": intent.sort.value}
    cats = [c for c in intent.categories if c.split(":")[0] in ("cuisine", "place", "event", "property", "mode")]
    allowed = CATEGORIES.get(dom, [])
    for c in cats:
        token = c.replace(":", ".", 1)
        if token in allowed:
            d["cat"] = token
            break
    if intent.plan_kind is not None:
        d["cat"] = f"plan.{intent.plan_kind.value}"
    if "cat" not in d and (not allowed or "any" in allowed):
        d["cat"] = "any"
    for attr_, key in (("location", "city"), ("origin", "origin"), ("destination", "dest")):
        ref = getattr(intent, attr_)
        if ref is not None and ref.place is not None:
            d[key] = ref.place.model_dump(mode="json")
    if dom.is_transport:
        d.pop("city", None)
    if intent.stay is not None:
        d["day"] = intent.stay.check_in.isoformat()
        d["nights"] = intent.stay.nights
    elif intent.when is not None and intent.when.start is not None:
        d["day"] = intent.when.start.date().isoformat()
        if (intent.when.start.hour, intent.when.start.minute) != (0, 0):
            d["time"] = intent.when.start.strftime("%H:%M")
    d["pax"] = intent.party.size
    rooms = intent.requirement("bedrooms.min")
    if rooms is not None:
        d["rooms"] = int(rooms.value)
    d["flags"] = [c.key for c in intent.requirements if c.key in FILTERS.get(dom, [])]
    return d
