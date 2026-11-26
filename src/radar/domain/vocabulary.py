"""Controlled vocabulary of constraint keys.

One list drives the rule-based parser, the filter buttons,
result attributes and filtering. Adding a constraint means adding it here and
teaching at least one provider to report it; until a provider reports it,
results carry Tri.UNKNOWN for that key and it can never produce a strict match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from radar.domain.common import Domain

D = Domain


class ValueType(StrEnum):
    FLAG = "flag"  # boolean requirement, evaluated against a Tri attribute
    INT_MIN = "int_min"
    INT_MAX = "int_max"
    NUM_MIN = "num_min"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class ConstraintDef:
    key: str
    domains: frozenset[Domain]
    value_type: ValueType = ValueType.FLAG
    # Some wishes are inherently subjective; we accept them only as preferences.
    preference_only: bool = False
    # Category-sensitive keys get extra privacy handling (no logging of raw values).
    sensitive: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _d(*ds: Domain) -> frozenset[Domain]:
    return frozenset(ds)


_PLACES = _d(D.DINING, D.WELLNESS, D.EVENT, D.SHOPPING)

CONSTRAINTS: dict[str, ConstraintDef] = {
    c.key: c
    for c in [
        # dining
        ConstraintDef("outdoor_seating", _d(D.DINING)),
        ConstraintDef("indoor_seating", _d(D.DINING)),
        ConstraintDef("private_dining", _d(D.DINING)),
        ConstraintDef("reservations", _d(D.DINING, D.WELLNESS)),
        ConstraintDef("takeaway", _d(D.DINING)),
        ConstraintDef("delivery", _d(D.DINING)),
        ConstraintDef("diet.vegetarian", _d(D.DINING)),
        ConstraintDef("diet.vegan", _d(D.DINING)),
        ConstraintDef("diet.halal", _d(D.DINING)),
        ConstraintDef("diet.kosher", _d(D.DINING)),
        ConstraintDef("diet.gluten_free", _d(D.DINING)),
        ConstraintDef("child_friendly", _PLACES | _d(D.STAY)),
        ConstraintDef("open_at", _PLACES),
        ConstraintDef("atmosphere.romantic", _d(D.DINING, D.STAY, D.EVENT, D.PLAN), preference_only=True),
        ConstraintDef("atmosphere.cozy", _d(D.DINING, D.STAY), preference_only=True),
        ConstraintDef("atmosphere.luxury", _d(D.DINING, D.STAY), preference_only=True),
        ConstraintDef("atmosphere.casual", _d(D.DINING), preference_only=True),
        ConstraintDef("atmosphere.family", _d(D.DINING, D.EVENT, D.PLAN), preference_only=True),
        # shared amenities
        ConstraintDef("wheelchair", _PLACES | _d(D.STAY, D.RAIL, D.GROUND)),
        ConstraintDef("parking", _PLACES | _d(D.STAY, D.REAL_ESTATE)),
        ConstraintDef("pet_friendly", _PLACES | _d(D.STAY, D.REAL_ESTATE)),
        ConstraintDef("wifi", _d(D.DINING, D.STAY, D.REAL_ESTATE)),
        ConstraintDef("air_conditioning", _d(D.DINING, D.STAY, D.REAL_ESTATE)),
        # stay
        ConstraintDef("bed.queen", _d(D.STAY)),
        ConstraintDef("bed.king", _d(D.STAY)),
        ConstraintDef("bed.double", _d(D.STAY)),
        ConstraintDef("bed.twin", _d(D.STAY)),
        ConstraintDef("private_bathroom", _d(D.STAY)),
        ConstraintDef("bathtub", _d(D.STAY)),
        ConstraintDef("breakfast", _d(D.STAY)),
        ConstraintDef("kitchen", _d(D.STAY, D.REAL_ESTATE)),
        ConstraintDef("balcony", _d(D.STAY, D.REAL_ESTATE)),
        ConstraintDef("heating", _d(D.STAY, D.REAL_ESTATE)),
        ConstraintDef("pool", _d(D.STAY, D.WELLNESS)),
        ConstraintDef("spa", _d(D.STAY)),
        ConstraintDef("late_checkin", _d(D.STAY)),
        ConstraintDef("early_checkin", _d(D.STAY)),
        ConstraintDef("free_cancellation", _d(D.STAY, D.EVENT, D.WELLNESS)),
        ConstraintDef("pay_at_property", _d(D.STAY)),
        ConstraintDef("entire_place", _d(D.STAY)),
        ConstraintDef("rating.min", _d(D.DINING, D.STAY, D.WELLNESS), ValueType.NUM_MIN),
        # real estate
        ConstraintDef("bedrooms.min", _d(D.REAL_ESTATE, D.STAY), ValueType.INT_MIN),
        ConstraintDef("rooms.min", _d(D.REAL_ESTATE), ValueType.INT_MIN),
        ConstraintDef("area.min", _d(D.REAL_ESTATE), ValueType.NUM_MIN),
        ConstraintDef("furnished", _d(D.REAL_ESTATE)),
        ConstraintDef("elevator", _d(D.REAL_ESTATE)),
        ConstraintDef("deal.rent", _d(D.REAL_ESTATE)),
        ConstraintDef("deal.sale", _d(D.REAL_ESTATE)),
        # transport
        ConstraintDef("direct", _d(D.FLIGHT, D.RAIL, D.GROUND)),
        ConstraintDef("max_transfers", _d(D.FLIGHT, D.RAIL, D.GROUND), ValueType.INT_MAX),
        ConstraintDef("max_duration_min", _d(D.FLIGHT, D.RAIL, D.GROUND), ValueType.INT_MAX),
        ConstraintDef("cabin", _d(D.FLIGHT), ValueType.TEXT),
        ConstraintDef("checked_bag", _d(D.FLIGHT)),
        ConstraintDef("refundable", _d(D.FLIGHT, D.RAIL, D.STAY, D.EVENT)),
        ConstraintDef("changeable", _d(D.FLIGHT, D.RAIL)),
        ConstraintDef("first_class", _d(D.RAIL)),
        ConstraintDef("sleeper", _d(D.RAIL)),
        ConstraintDef("bicycle", _d(D.RAIL, D.GROUND)),
        ConstraintDef("arrive_before", _d(D.FLIGHT, D.RAIL, D.GROUND), ValueType.TEXT),
        ConstraintDef("depart_after", _d(D.FLIGHT, D.RAIL, D.GROUND), ValueType.TEXT),
        # events / wellness
        ConstraintDef("indoor", _d(D.EVENT, D.WELLNESS)),
        ConstraintDef("outdoor", _d(D.EVENT)),
        ConstraintDef("sauna", _d(D.WELLNESS, D.STAY)),
        # Staff-gender requests are only ever answered from provider-side data.
        ConstraintDef("staff.female", _d(D.WELLNESS), sensitive=True),
        ConstraintDef("staff.male", _d(D.WELLNESS), sensitive=True),
        # shopping
        ConstraintDef("in_stock", _d(D.SHOPPING)),
        ConstraintDef("condition.used", _d(D.SHOPPING)),
        ConstraintDef("condition.new", _d(D.SHOPPING)),
    ]
}


def keys_for(domain: Domain) -> list[str]:
    return [k for k, c in CONSTRAINTS.items() if domain in c.domains]


def is_known(key: str) -> bool:
    return key in CONSTRAINTS
