"""Duffel Flights API: live offers, offer revalidation and order creation.

Docs: https://duffel.com/docs/api (Duffel-Version: v2). Self-serve sign-up
gives a test-mode token (fake airline "Duffel Airways", unlimited balance);
live mode needs account verification. Offers are purchasable until
`expires_at`, prices include taxes, and an offer is always one ticket, so
connections inside it are protected (not self-transfer).

Status in this repository: implemented against the documented schema and
covered by contract tests with recorded-shape payloads. It has not been run
against the live API from this repository because no token is configured.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from radar.config import get_settings
from radar.core.errors import BookingError, ProviderError
from radar.core.money import Money, MoneyError
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.core.timeutil import tz
from radar.domain.common import Domain
from radar.domain.intents import SearchIntent
from radar.domain.results import (
    Availability,
    BookingMode,
    Connection,
    Leg,
    Link,
    LinkKind,
    Price,
    PriceKind,
    Result,
    Stop,
)
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

BASE = "https://api.duffel.com"
_DUR = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?")


def iso_duration_min(value: str | None) -> int | None:
    if not value:
        return None
    m = _DUR.match(value)
    if not m:
        return None
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return d * 1440 + h * 60 + mi


def _local(value: str | None, zone: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz(zone))


def _cabin(intent: SearchIntent) -> str | None:
    c = intent.requirement("cabin")
    if c is None:
        return None
    v = str(c.value).lower()
    return v if v in {"economy", "premium_economy", "business", "first"} else None


def offer_to_result(offer: dict[str, Any], live_mode: bool) -> Result | None:
    try:
        total = Money.of(str(offer["total_amount"]), str(offer["total_currency"]))
    except (KeyError, MoneyError):
        return None
    legs: list[Leg] = []
    connections: list[Connection] = []
    checked_bags: list[bool] = []
    for sl in offer.get("slices", []):
        segs = sl.get("segments", [])
        for i, seg in enumerate(segs):
            o, d = seg.get("origin", {}), seg.get("destination", {})
            carrier = (seg.get("marketing_carrier") or {}).get("name")
            code = (seg.get("marketing_carrier") or {}).get("iata_code") or ""
            number = seg.get("marketing_carrier_flight_number") or ""
            legs.append(
                Leg(
                    mode="flight",
                    carrier=carrier,
                    service=f"{code}{number}" or None,
                    origin=Stop(
                        name=o.get("name") or o.get("iata_code", "?"),
                        code=o.get("iata_code"),
                        time=_local(seg.get("departing_at"), o.get("time_zone")),
                    ),
                    destination=Stop(
                        name=d.get("name") or d.get("iata_code", "?"),
                        code=d.get("iata_code"),
                        time=_local(seg.get("arriving_at"), d.get("time_zone")),
                    ),
                    duration_min=iso_duration_min(seg.get("duration")),
                )
            )
            for p in seg.get("passengers", []):
                bags = p.get("baggages") or []
                checked_bags.append(any(b.get("type") == "checked" and int(b.get("quantity", 0)) > 0 for b in bags))
            if i + 1 < len(segs):
                nxt = segs[i + 1]
                a = _local(seg.get("arriving_at"), d.get("time_zone"))
                b = _local(nxt.get("departing_at"), (nxt.get("origin") or {}).get("time_zone"))
                connections.append(
                    Connection(
                        at=d.get("iata_code") or "?",
                        minutes=int((b - a).total_seconds() // 60) if a and b else None,
                        self_transfer=Tri.NO,
                        station_change=(d.get("iata_code") != (nxt.get("origin") or {}).get("iata_code")),
                        overnight=bool(a and b and a.date() != b.date()),
                    )
                )
    if not legs:
        return None
    cond = offer.get("conditions") or {}
    refund = (cond.get("refund_before_departure") or {}).get("allowed")
    change = (cond.get("change_before_departure") or {}).get("allowed")
    first_slice = (offer.get("slices") or [{}])[0]
    attrs = {
        "direct": Tri.YES if all(len(s.get("segments", [])) == 1 for s in offer.get("slices", [])) else Tri.NO,
        "refundable": Tri.YES if refund is True else Tri.NO if refund is False else Tri.UNKNOWN,
        "changeable": Tri.YES if change is True else Tri.NO if change is False else Tri.UNKNOWN,
        "checked_bag": Tri.YES if checked_bags and all(checked_bags) else Tri.NO if checked_bags else Tri.UNKNOWN,
    }
    owner = (offer.get("owner") or {}).get("name")
    return Result(
        domain=Domain.FLIGHT,
        title=" / ".join(x.service or "?" for x in legs),
        subtitle=f"{legs[0].origin.code} -> {legs[-1].destination.code}" + (f" ({owner})" if owner else ""),
        starts_at=legs[0].origin.time,
        ends_at=_local(
            first_slice.get("segments", [{}])[-1].get("arriving_at"),
            (first_slice.get("destination") or {}).get("time_zone"),
        )
        or legs[-1].destination.time,
        legs=legs,
        connections=connections,
        price=Price(amount=total, kind=PriceKind.OFFER, includes_taxes=Tri.YES, unknown_fees=False),
        attributes=attrs,
        availability=Availability.CONFIRMED,
        booking=BookingMode.DIRECT,
        links=[Link(kind=LinkKind.SOURCE, url="https://duffel.com", label="Duffel")],
        facts={
            "expires_at": offer.get("expires_at"),
            "live_mode": live_mode,
            "passenger_ids": [p.get("id") for p in offer.get("passengers", [])],
        },
        provenance=Provenance(
            provider="duffel",
            freshness=Freshness.LIVE if live_mode else Freshness.FIXTURE,
            source_url="https://duffel.com",
            attribution="Duffel" + ("" if live_mode else " (test mode)"),
            external_id=str(offer.get("id")),
            retrieved_at=utcnow(),
        ),
    )


class DuffelProvider(Provider):
    name = "duffel"
    display_name = "Duffel"
    access = AccessLevel.SANDBOX
    docs_url = "https://duffel.com/docs/api"
    capabilities = (
        Capability(
            Domain.FLIGHT,
            supports_booking=True,
            supports_revalidation=True,
            # Offer requests are real airline shopping calls; scheduled polling is a cost and
            # look-to-book concern, so radars use cached fare sources instead.
            monitoring_allowed=False,
            cost_units=5.0,
        ),
    )

    def __init__(self, token: str | None = None, http: ProviderHttp | None = None) -> None:
        s = get_settings()
        self.token = token or (s.duffel_token.get_secret_value() if s.duffel_token else None)
        self.http = http or ProviderHttp("duffel", rate_per_s=2, burst=4, max_retries=1, timeout_s=45)

    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Duffel-Version": "v2",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        self.ensure_configured()
        o, d = intent.origin, intent.destination
        if not (
            o and o.place and o.place.iata and d and d.place and d.place.iata and intent.when and intent.when.start
        ):
            return []
        passengers: list[dict[str, Any]] = [{"type": "adult"} for _ in range(intent.party.adults)]
        passengers += [{"age": max(0, a)} for a in intent.party.children_ages]
        body: dict[str, Any] = {
            "data": {
                "slices": [
                    {
                        "origin": o.place.iata,
                        "destination": d.place.iata,
                        "departure_date": intent.when.start.date().isoformat(),
                    }
                ],
                "passengers": passengers,
                "max_connections": 0 if intent.has_requirement("direct") else 1,
            }
        }
        if cabin := _cabin(intent):
            body["data"]["cabin_class"] = cabin
        resp = await self.http.request(
            "POST",
            f"{BASE}/air/offer_requests",
            params={"return_offers": "true", "supplier_timeout": 20000},
            json=body,
            headers=self._headers(),
        )
        data = resp.json().get("data", {})
        live = bool(data.get("live_mode"))
        out = [r for r in (offer_to_result(of, live) for of in data.get("offers", [])) if r]
        return out[: ctx.max_results]

    async def revalidate(self, result: Result, ctx: SearchContext) -> Result:
        self.ensure_configured()
        offer_id = result.provenance.external_id
        resp = await self.http.request("GET", f"{BASE}/air/offers/{offer_id}", headers=self._headers())
        data = resp.json().get("data", {})
        fresh = offer_to_result(data, bool(data.get("live_mode")))
        if fresh is None:
            raise BookingError("offer is no longer available", code="offer_gone")
        return fresh

    async def create_order(
        self, offer: Result, passengers: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        """Instant order paid from the Duffel balance. `passengers` must carry the passenger ids
        from the offer plus given_name, family_name, born_on, gender, title, email, phone_number."""
        self.ensure_configured()
        assert offer.price is not None
        body = {
            "data": {
                "type": "instant",
                "selected_offers": [offer.provenance.external_id],
                "passengers": passengers,
                "payments": [
                    {
                        "type": "balance",
                        "amount": str(offer.price.amount.amount),
                        "currency": offer.price.amount.currency,
                    }
                ],
                "metadata": {"radar_idempotency_key": idempotency_key},
            }
        }
        try:
            resp = await self.http.request("POST", f"{BASE}/air/orders", json=body, headers=self._headers())
        except ProviderError as exc:
            raise BookingError(f"airline rejected the order ({exc.code})") from exc
        data = resp.json().get("data", {})
        return {
            "total_amount": data.get("total_amount"),
            "total_currency": data.get("total_currency"),
            "reference": data.get("booking_reference"),
            "order_id": data.get("id"),
            "status": "confirmed" if data.get("booking_reference") else "submitted",
        }

    async def aclose(self) -> None:
        await self.http.aclose()
