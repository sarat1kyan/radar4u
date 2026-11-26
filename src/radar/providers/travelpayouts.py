"""Aviasales Data API (Travelpayouts): cached fares found by users in the
last ~48 hours. These are price estimates, not offers: they can be gone or
different when the user clicks through, and RADAR labels them accordingly.
Good fit for price-alert radars because the provider is built for repeated
queries (documented limit 600 req/min on prices_for_dates).

Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163
Auth: X-Access-Token header. Free partner account. No booking API; the
purchase happens on the Aviasales site via the returned link.

Status in this repository: implemented against the documented endpoint and
envelope; per-item field names are parsed defensively. Not run live here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from radar.config import get_settings
from radar.core.money import Money, MoneyError
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.domain.common import Domain
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, BookingMode, Leg, Link, LinkKind, Price, PriceKind, Result, Stop
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

BASE = "https://api.travelpayouts.com"


def _dt(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def item_to_result(item: dict[str, Any], currency: str) -> Result | None:
    try:
        price = Money.of(str(item["price"]), currency)
    except (KeyError, MoneyError):
        return None
    dep = _dt(item.get("departure_at"))
    origin = str(item.get("origin_airport") or item.get("origin") or "?")
    dest = str(item.get("destination_airport") or item.get("destination") or "?")
    airline = item.get("airline")
    number = item.get("flight_number")
    transfers = item.get("transfers")
    duration = item.get("duration_to") or item.get("duration")
    link = item.get("link")
    url = f"https://www.aviasales.com{link}" if isinstance(link, str) and link.startswith("/") else None
    links = [Link(kind=LinkKind.BOOKING, url=url, label="Aviasales")] if url else []
    links.append(Link(kind=LinkKind.SOURCE, url="https://www.aviasales.com", label="Aviasales data"))
    return Result(
        domain=Domain.FLIGHT,
        title=f"{airline or ''}{number or ''}".strip() or f"{origin}-{dest}",
        subtitle=f"{origin} → {dest}",
        starts_at=dep,
        legs=[
            Leg(
                mode="flight",
                carrier=airline,
                service=f"{airline}{number}" if airline and number else None,
                origin=Stop(name=origin, code=origin, time=dep),
                destination=Stop(name=dest, code=dest),
                duration_min=int(duration) if isinstance(duration, int) else None,
            )
        ],
        price=Price(
            amount=price,
            kind=PriceKind.ESTIMATE,
            includes_taxes=Tri.UNKNOWN,
            unknown_fees=True,
            conditions=["cached fare, recheck on the booking site"],
        ),
        attributes={"direct": Tri.YES if transfers == 0 else Tri.NO if isinstance(transfers, int) else Tri.UNKNOWN},
        availability=Availability.UNKNOWN,
        booking=BookingMode.HANDOFF if url else BookingMode.NONE,
        links=links,
        facts={"transfers": transfers, "found_at": item.get("found_at")},
        provenance=Provenance(
            provider="travelpayouts",
            freshness=Freshness.CACHED_ESTIMATE,
            source_url="https://www.aviasales.com",
            attribution="Fare data by Aviasales / Travelpayouts",
            external_id=f"{origin}-{dest}-{item.get('departure_at')}-{airline}{number}",
            retrieved_at=utcnow(),
        ),
    )


class TravelpayoutsProvider(Provider):
    name = "travelpayouts"
    display_name = "Aviasales Data API"
    access = AccessLevel.SELF_SERVE_KEY
    docs_url = "https://support.travelpayouts.com/hc/en-us/articles/203956163"
    capabilities = (
        Capability(Domain.FLIGHT, monitoring_allowed=True, min_monitor_interval_s=3 * 3600, cost_units=0.2),
    )

    def __init__(self, token: str | None = None, http: ProviderHttp | None = None) -> None:
        s = get_settings()
        self.token = token or (s.travelpayouts_token.get_secret_value() if s.travelpayouts_token else None)
        self.http = http or ProviderHttp("travelpayouts", rate_per_s=5, burst=5, max_retries=1)

    def configured(self) -> bool:
        return bool(self.token)

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        self.ensure_configured()
        o, d = intent.origin, intent.destination
        if not (o and o.place and o.place.iata and d and d.place and d.place.iata):
            return []
        currency = ctx.currency.upper()
        params: dict[str, Any] = {
            "origin": o.place.iata,
            "destination": d.place.iata,
            "one_way": "true",
            "sorting": "price",
            "currency": currency.lower(),
            "limit": min(30, ctx.max_results),
        }
        if intent.when and intent.when.start:
            span = (intent.when.end - intent.when.start).days if intent.when.end else 0
            fmt = "%Y-%m" if span > 3 else "%Y-%m-%d"
            params["departure_at"] = intent.when.start.strftime(fmt)
        if intent.has_requirement("direct"):
            params["direct"] = "true"
        data = await self.http.get_json(
            f"{BASE}/aviasales/v3/prices_for_dates", params=params, headers={"X-Access-Token": str(self.token)}
        )
        if not data.get("success"):
            return []
        cur = str(data.get("currency") or currency).upper()
        return [r for r in (item_to_result(it, cur) for it in data.get("data", [])) if r]

    async def aclose(self) -> None:
        await self.http.aclose()
