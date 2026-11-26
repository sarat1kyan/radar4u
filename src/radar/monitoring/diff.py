"""Material-change detection between radar checks (pure functions).

Only exact matches can trigger an alert: a result with an unverified
requirement is not something we tell people "matches". The first check is a
baseline for new-result radars (no flood of "new" alerts), while price and
availability radars alert immediately if the condition already holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from radar.core.money import Money
from radar.domain.intents import MonitorKind
from radar.domain.results import Availability
from radar.search.filters import Tier
from radar.search.ranking import Ranked

# A price move smaller than this (relative) is noise, not news.
MATERIAL_PRICE_DELTA = Decimal("0.01")
# Listings missing from this many consecutive checks are considered gone/stale.
STALE_AFTER_CHECKS = 3


@dataclass(frozen=True)
class PrevObs:
    price: Decimal | None
    currency: str | None
    availability: str
    missing_checks: int = 0


@dataclass
class Event:
    kind: str  # price_drop | now_available | new_result | gone
    key: str
    title: str
    old: Money | None = None
    new: Money | None = None
    ranked: Ranked | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def comparable_price(item: Ranked, currency: str | None) -> Money | None:
    """Price in the radar's threshold currency/unit when we can express it, else original."""
    if item.report.price_in_budget_ccy is not None:
        return item.report.price_in_budget_ccy
    if item.result.price is None:
        return None
    if currency and item.converted and item.converted.converted.currency == currency:
        return item.converted.converted
    return item.result.price.amount


def detect(
    kind: MonitorKind,
    threshold: Money | None,
    previous: dict[str, PrevObs],
    current: list[Ranked],
    *,
    baseline: bool,
) -> list[Event]:
    matches = [r for r in current if r.tier is Tier.EXACT]
    events: list[Event] = []
    seen: set[str] = set()
    for item in matches:
        key = item.result.id
        seen.add(key)
        prev = previous.get(key)
        price = comparable_price(item, threshold.currency if threshold else None)
        if kind is MonitorKind.PRICE_BELOW:
            if threshold is None or price is None or price.currency != threshold.currency:
                continue
            if price.amount > threshold.amount:
                continue
            if prev is None or prev.price is None or prev.currency != price.currency:
                events.append(Event("price_drop", key, item.result.title, None, price, item))
            elif prev.price > threshold.amount or (prev.price - price.amount) >= prev.price * MATERIAL_PRICE_DELTA:
                events.append(
                    Event(
                        "price_drop",
                        key,
                        item.result.title,
                        Money(amount=prev.price, currency=price.currency),
                        price,
                        item,
                    )
                )
        elif kind is MonitorKind.AVAILABILITY:
            if item.result.availability is Availability.CONFIRMED and (
                prev is None or prev.availability != Availability.CONFIRMED.value
            ):
                events.append(Event("now_available", key, item.result.title, None, price, item))
        elif kind is MonitorKind.NEW_RESULTS and prev is None and not baseline:
            events.append(Event("new_result", key, item.result.title, None, price, item))
    if kind is MonitorKind.NEW_RESULTS:
        for key, prev in previous.items():
            if key not in seen and prev.missing_checks + 1 == STALE_AFTER_CHECKS:
                events.append(Event("gone", key, "", extra={"stale": True}))
    return events
