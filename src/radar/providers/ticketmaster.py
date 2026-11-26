"""Ticketmaster Discovery API v2: event search with a hand-off to the official
ticket page. Free self-serve key (5000 calls/day, 5 req/s). The API lists
events and price ranges; it does not reserve seats, so availability stays
UNKNOWN and prices are "from" ranges.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from radar.config import get_settings
from radar.core.money import Money, MoneyError
from radar.core.provenance import Freshness, Provenance, utcnow
from radar.domain.common import Domain, GeoPoint
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, BookingMode, Link, LinkKind, Price, PriceKind, Result
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

BASE = "https://app.ticketmaster.com/discovery/v2"
_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"

CLASSIFICATION = {
    "event:concert": "Music",
    "event:festival": "Music",
    "event:theatre": "Arts & Theatre",
    "event:sports": "Sports",
    "event:cinema": "Film",
}


def geohash(lat: float, lon: float, precision: int = 7) -> str:
    lat_rng, lon_rng = [-90.0, 90.0], [-180.0, 180.0]
    bits, bit, ch, even = [16, 8, 4, 2, 1], 0, 0, True
    out: list[str] = []
    while len(out) < precision:
        rng, val = (lon_rng, lon) if even else (lat_rng, lat)
        mid = (rng[0] + rng[1]) / 2
        if val > mid:
            ch |= bits[bit]
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_B32[ch])
            bit, ch = 0, 0
    return "".join(out)


def event_to_result(ev: dict[str, Any], origin: GeoPoint | None) -> Result | None:
    name = ev.get("name")
    if not name:
        return None
    start = (ev.get("dates") or {}).get("start") or {}
    starts_at = None
    if start.get("dateTime"):
        try:
            starts_at = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        except ValueError:
            starts_at = None
    venues = (ev.get("_embedded") or {}).get("venues") or [{}]
    venue = venues[0]
    loc = venue.get("location") or {}
    point = None
    try:
        point = GeoPoint(lat=float(loc["latitude"]), lon=float(loc["longitude"]))
    except (KeyError, TypeError, ValueError):
        point = None
    price = None
    ranges = ev.get("priceRanges") or []
    if ranges:
        try:
            price = Price(amount=Money.of(str(ranges[0]["min"]), str(ranges[0]["currency"])), kind=PriceKind.FROM)
        except (KeyError, MoneyError):
            price = None
    status = ((ev.get("dates") or {}).get("status") or {}).get("code")
    classes = ev.get("classifications") or [{}]
    genre = ((classes[0].get("genre") or {}).get("name")) if classes else None
    url = ev.get("url")
    links = [Link(kind=LinkKind.BOOKING, url=url, label="Ticketmaster")] if isinstance(url, str) else []
    return Result(
        domain=Domain.EVENT,
        title=str(name),
        subtitle=", ".join(x for x in (venue.get("name"), genre) if x) or None,
        point=point,
        address=(venue.get("address") or {}).get("line1"),
        distance_m=round(origin.distance_m(point)) if origin and point else None,
        starts_at=starts_at,
        price=price,
        availability=Availability.NOT_AVAILABLE if status in ("cancelled", "offsale") else Availability.UNKNOWN,
        booking=BookingMode.HANDOFF if links else BookingMode.NONE,
        links=links,
        facts={"sale_status": status, "local_date": start.get("localDate"), "local_time": start.get("localTime")},
        provenance=Provenance(
            provider="ticketmaster",
            freshness=Freshness.LIVE,
            source_url=url if isinstance(url, str) else "https://www.ticketmaster.com",
            attribution="Ticketmaster",
            external_id=str(ev.get("id")),
            retrieved_at=utcnow(),
        ),
    )


class TicketmasterProvider(Provider):
    name = "ticketmaster"
    display_name = "Ticketmaster"
    access = AccessLevel.SELF_SERVE_KEY
    docs_url = "https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/"
    capabilities = (Capability(Domain.EVENT, monitoring_allowed=True, min_monitor_interval_s=6 * 3600),)

    def __init__(self, key: str | None = None, http: ProviderHttp | None = None) -> None:
        s = get_settings()
        self.key = key or (s.ticketmaster_key.get_secret_value() if s.ticketmaster_key else None)
        self.http = http or ProviderHttp("ticketmaster", rate_per_s=4, burst=4, max_retries=1)

    def configured(self) -> bool:
        return bool(self.key)

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        self.ensure_configured()
        ref = intent.location
        if not (ref and ref.place):
            return []
        start = intent.when.start if intent.when and intent.when.start else datetime.now(UTC)
        end = (
            intent.when.end
            if intent.when and intent.when.end and intent.when.end > start
            else start + timedelta(days=14)
        )
        params: dict[str, Any] = {
            "apikey": self.key,
            "geoPoint": geohash(ref.place.point.lat, ref.place.point.lon),
            "radius": max(1, (ref.radius_m or 25_000) // 1000),
            "unit": "km",
            "startDateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": min(50, ctx.max_results),
            "sort": "date,asc",
            "locale": "*",
        }
        for c in intent.categories:
            if c in CLASSIFICATION:
                params["classificationName"] = CLASSIFICATION[c]
                break
        data = await self.http.get_json(f"{BASE}/events.json", params=params)
        events = ((data or {}).get("_embedded") or {}).get("events") or []
        return [r for r in (event_to_result(e, ref.place.point) for e in events) if r]

    async def aclose(self) -> None:
        await self.http.aclose()
