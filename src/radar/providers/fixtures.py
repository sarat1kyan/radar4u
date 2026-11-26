"""Development fixtures for domains without a configured live provider.

Enabled only with RADAR_ENABLE_FIXTURES=true and never in prod. Every result
is FIXTURE freshness, titled with a [DEMO] prefix and rendered with a visible
"demo data" label, so it cannot be mistaken for a real offer. Values are
generated deterministically from the request so tests and demos are stable.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from radar.core.errors import BookingError
from radar.core.money import Money
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.domain.common import BudgetUnit, Domain, GeoPoint
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, BookingMode, Leg, Price, PriceKind, Rating, Result, Stop
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext

PREFIX = "[DEMO] "


def _rnd(seed: str, n: int) -> int:
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % n


class FixtureProvider(Provider):
    name = "fixtures"
    display_name = "Demo data"
    access = AccessLevel.FIXTURE
    capabilities = (
        Capability(
            Domain.STAY,
            supports_booking=True,
            supports_revalidation=True,
            monitoring_allowed=True,
            min_monitor_interval_s=60,
            cost_units=0.0,
        ),
        Capability(
            Domain.FLIGHT,
            supports_booking=True,
            supports_revalidation=True,
            monitoring_allowed=True,
            min_monitor_interval_s=60,
            cost_units=0.0,
        ),
        Capability(Domain.REAL_ESTATE, monitoring_allowed=True, min_monitor_interval_s=60, cost_units=0.0),
        Capability(Domain.SHOPPING, monitoring_allowed=True, min_monitor_interval_s=60, cost_units=0.0),
    )

    def __init__(self) -> None:
        # Tests may bump this to simulate price movements between radar checks.
        self.price_shift = Decimal(0)
        self.bookings: dict[str, dict[str, Any]] = {}

    def _prov(self, ext: str) -> Provenance:
        return Provenance(
            provider=self.name,
            freshness=Freshness.FIXTURE,
            external_id=ext,
            retrieved_at=utcnow(),
            attribution="Demo data, not a real offer",
        )

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        if intent.domain is Domain.STAY:
            return self._stays(intent, ctx)
        if intent.domain is Domain.FLIGHT:
            return self._flights(intent, ctx)
        if intent.domain is Domain.REAL_ESTATE:
            return self._listings(intent, ctx)
        if intent.domain is Domain.SHOPPING:
            return self._products(intent, ctx)
        return []

    def _stays(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        ref = intent.location
        if not (ref and ref.place and intent.stay):
            return []
        nights = intent.stay.nights
        cur = ctx.currency.upper()
        out = []
        names = ["Central Suites", "Station Hotel", "Old Town Apartments", "Riverside Inn", "Garden Guesthouse"]
        for i, name in enumerate(names):
            seed = f"{ref.place.canonical_id}:{name}"
            nightly = Decimal(60 + _rnd(seed, 140)) + self.price_shift
            p = ref.place.point
            point = GeoPoint(lat=p.lat + (i - 2) * 0.004, lon=p.lon + (_rnd(seed + "x", 9) - 4) * 0.004)
            attrs = {
                "wifi": Tri.YES,
                "bed.queen": Tri.YES if i in (0, 2) else Tri.NO if i == 1 else Tri.UNKNOWN,
                "private_bathroom": Tri.YES if i != 4 else Tri.NO,
                "breakfast": Tri.YES if i in (1, 3) else Tri.NO,
                "balcony": Tri.YES if i == 2 else Tri.UNKNOWN,
                "late_checkin": Tri.YES if i in (1, 0) else Tri.UNKNOWN,
                "free_cancellation": Tri.YES if i % 2 == 0 else Tri.NO,
            }
            out.append(
                Result(
                    domain=Domain.STAY,
                    title=PREFIX + name,
                    subtitle="Double room",
                    point=point,
                    distance_m=round(p.distance_m(point)),
                    price=Price(
                        amount=Money.of(nightly * nights, cur),
                        unit=BudgetUnit.TOTAL,
                        kind=PriceKind.OFFER,
                        includes_taxes=Tri.YES,
                        unknown_fees=False,
                    ),
                    rating=Rating(
                        value=round(7.0 + _rnd(seed, 28) / 10, 1), scale=10, count=40 + _rnd(seed, 900), source="demo"
                    ),
                    attributes=attrs,
                    availability=Availability.CONFIRMED,
                    booking=BookingMode.DIRECT,
                    facts={"nights": nights, "fixture": True},
                    provenance=self._prov(f"stay:{seed}:{intent.stay.check_in}"),
                )
            )
        return out

    def _flights(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        o, d = intent.origin, intent.destination
        if not (o and o.place and d and d.place):
            return []
        if not (intent.when and intent.when.start):
            return []
        start: datetime = intent.when.start
        km = o.place.point.distance_m(d.place.point) / 1000
        out = []
        for i, (hour, stops) in enumerate([(7, 0), (11, 1), (16, 0), (21, 1)]):
            seed = f"{o.place.canonical_id}-{d.place.canonical_id}-{hour}"
            dep = start.replace(hour=hour, minute=5 * _rnd(seed, 12), second=0, microsecond=0)
            dur = int(km / 12) + 40 + stops * 90
            arr = dep + timedelta(minutes=dur)
            price = Decimal(45 + int(km / 25) + _rnd(seed, 120) - stops * 30) + self.price_shift
            oc, dc = o.place.iata or "AAA", d.place.iata or "BBB"
            out.append(
                Result(
                    domain=Domain.FLIGHT,
                    title=f"{PREFIX}XX{100 + i}",
                    subtitle=f"{oc} -> {dc}",
                    starts_at=dep,
                    ends_at=arr,
                    legs=[
                        Leg(
                            mode="flight",
                            carrier="Demo Air",
                            service=f"XX{100 + i}",
                            origin=Stop(name=oc, code=oc, time=dep),
                            destination=Stop(name=dc, code=dc, time=arr),
                            duration_min=dur,
                        )
                    ],
                    price=Price(
                        amount=Money.of(max(Decimal(19), price), ctx.currency),
                        kind=PriceKind.OFFER,
                        includes_taxes=Tri.YES,
                        unknown_fees=False,
                    ),
                    attributes={
                        "direct": Tri.YES if stops == 0 else Tri.NO,
                        "refundable": Tri.NO,
                        "checked_bag": Tri.YES if i % 2 else Tri.NO,
                    },
                    availability=Availability.CONFIRMED,
                    booking=BookingMode.DIRECT,
                    facts={"fixture": True, "stops": stops},
                    provenance=self._prov(f"flight:{seed}:{dep.date()}"),
                )
            )
        return out

    def _listings(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        ref = intent.location
        if not (ref and ref.place):
            return []
        out = []
        for i in range(5):
            seed = f"{ref.place.canonical_id}:flat{i}"
            beds = 1 + _rnd(seed, 3)
            rent = Decimal(350 + _rnd(seed, 900)) + self.price_shift
            p = ref.place.point
            point = GeoPoint(lat=p.lat + (i - 2) * 0.006, lon=p.lon + (i - 2) * 0.005)
            out.append(
                Result(
                    domain=Domain.REAL_ESTATE,
                    title=f"{PREFIX}{beds}-bedroom apartment",
                    point=point,
                    distance_m=round(p.distance_m(point)),
                    price=Price(amount=Money.of(rent, "USD"), unit=BudgetUnit.MONTH, kind=PriceKind.OFFER),
                    attributes={
                        "balcony": Tri.YES if i % 2 == 0 else Tri.UNKNOWN,
                        "pet_friendly": Tri.YES if i < 3 else Tri.NO,
                        "furnished": Tri.YES,
                        "deal.rent": Tri.YES,
                        "deal.sale": Tri.NO,
                        "bedrooms.min": Tri.UNKNOWN,
                    },
                    facts={"bedrooms": beds, "fixture": True, "published_at": utcnow().isoformat()},
                    booking=BookingMode.NONE,
                    provenance=self._prov(f"flat:{seed}"),
                )
            )
        return out

    def _products(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        q = (intent.raw_text or "item")[:40]
        return [
            Result(
                domain=Domain.SHOPPING,
                title=f"{PREFIX}{q}",
                price=Price(amount=Money.of(Decimal(99) + self.price_shift, ctx.currency), kind=PriceKind.OFFER),
                attributes={"in_stock": Tri.YES},
                availability=Availability.CONFIRMED,
                provenance=self._prov(f"product:{q}"),
            )
        ]

    async def revalidate(self, result: Result, ctx: SearchContext) -> Result:
        fresh = result.model_copy(deep=True)
        if fresh.price is not None and self.price_shift:
            fresh.price = fresh.price.model_copy(
                update={"amount": Money.of(fresh.price.amount.amount + self.price_shift, fresh.price.amount.currency)}
            )
        fresh.provenance = fresh.provenance.model_copy(update={"retrieved_at": utcnow()})
        return fresh

    async def book(self, result: Result, idempotency_key: str) -> dict[str, Any]:
        if idempotency_key in self.bookings:
            return self.bookings[idempotency_key]
        if "fail" in result.title.lower():
            raise BookingError("demo provider rejected the booking")
        ref = "DEMO-" + hashlib.sha1(idempotency_key.encode(), usedforsecurity=False).hexdigest()[:6].upper()
        self.bookings[idempotency_key] = {"reference": ref, "status": "confirmed"}
        return self.bookings[idempotency_key]
