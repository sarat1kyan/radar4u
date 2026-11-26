"""Permitted listing feeds (real estate, second-hand goods, local deals).

Marketplaces such as List.am, Myhome.ge, SS.ge and Krisha.kz publish no public
API, and RADAR does not scrape them. Listings reach RADAR only through:
  * feeds a partner or merchant explicitly publishes for us (this adapter),
  * listings users submit themselves (stored in the database),
  * official links the user opens on the marketplace itself.

Feed format (JSON, UTF-8), one object per listing:
  {"id": "...", "title": "...", "url": "https://...", "domain": "realestate"|"shopping",
   "price": {"amount": "800", "currency": "USD", "unit": "month"|"total"},
   "lat": 41.7, "lon": 44.8, "address": "...", "city": "Tbilisi", "country": "GE",
   "bedrooms": 2, "rooms": 3, "area_m2": 70, "deal": "rent"|"sale",
   "furnished": true, "pets": true, "balcony": true, "parking": false, "elevator": true,
   "in_stock": true, "condition": "used"|"new",
   "published_at": "2026-11-20T10:00:00Z", "updated_at": "..."}
Unknown or missing flags stay unknown.

Feeds are configured with RADAR_LISTING_FEEDS as a JSON list of
{"name", "url", "domain", "countries": [...], "attribution", "license"}.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from radar.core.money import Money, MoneyError
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.domain.common import BudgetUnit, Domain, GeoPoint
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, BookingMode, Link, LinkKind, Price, PriceKind, Result
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

_FLAG_KEYS = {
    "furnished": "furnished",
    "pets": "pet_friendly",
    "balcony": "balcony",
    "parking": "parking",
    "elevator": "elevator",
    "in_stock": "in_stock",
    "wifi": "wifi",
}


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    domain: Domain
    countries: frozenset[str] | None
    attribution: str
    license: str | None = None


def load_feed_configs(raw: str | None = None) -> list[FeedConfig]:
    raw = raw if raw is not None else os.environ.get("RADAR_LISTING_FEEDS", "")
    if not raw.strip():
        return []
    out = []
    for item in json.loads(raw):
        out.append(
            FeedConfig(
                name=str(item["name"]),
                url=str(item["url"]),
                domain=Domain(item.get("domain", "realestate")),
                countries=frozenset(c.upper() for c in item["countries"]) if item.get("countries") else None,
                attribution=str(item.get("attribution") or item["name"]),
                license=item.get("license"),
            )
        )
    return out


def _tri(v: Any) -> Tri:
    if v is True:
        return Tri.YES
    if v is False:
        return Tri.NO
    return Tri.UNKNOWN


def listing_to_result(
    item: dict[str, Any],
    provider: str,
    attribution: str,
    origin: GeoPoint | None,
    freshness: Freshness = Freshness.DIRECTORY,
) -> Result | None:
    title, url = item.get("title"), item.get("url")
    if not title or not isinstance(url, str) or not url.startswith("https://"):
        return None
    domain = Domain(item.get("domain", "realestate"))
    price = None
    p = item.get("price") or {}
    try:
        if p.get("amount") is not None:
            unit = BudgetUnit(p.get("unit", "month" if domain is Domain.REAL_ESTATE else "total"))
            price = Price(
                amount=Money.of(str(p["amount"]), str(p["currency"])),
                unit=unit,
                kind=PriceKind.OFFER,
                includes_taxes=Tri.UNKNOWN,
                unknown_fees=True,
            )
    except (KeyError, MoneyError, ValueError):
        price = None
    point = None
    if item.get("lat") is not None and item.get("lon") is not None:
        try:
            point = GeoPoint(lat=float(item["lat"]), lon=float(item["lon"]))
        except (TypeError, ValueError):
            point = None
    attrs = {key: _tri(item.get(src)) for src, key in _FLAG_KEYS.items()}
    deal = item.get("deal")
    attrs["deal.rent"] = Tri.YES if deal == "rent" else Tri.NO if deal == "sale" else Tri.UNKNOWN
    attrs["deal.sale"] = Tri.YES if deal == "sale" else Tri.NO if deal == "rent" else Tri.UNKNOWN
    cond = item.get("condition")
    attrs["condition.used"] = Tri.YES if cond == "used" else Tri.NO if cond == "new" else Tri.UNKNOWN
    attrs["condition.new"] = Tri.YES if cond == "new" else Tri.NO if cond == "used" else Tri.UNKNOWN
    facts = {
        k: item.get(k)
        for k in ("bedrooms", "rooms", "area_m2", "published_at", "updated_at", "city")
        if item.get(k) is not None
    }
    return Result(
        domain=domain,
        title=str(title)[:200],
        subtitle=", ".join(str(x) for x in (item.get("address"), item.get("city")) if x) or None,
        point=point,
        address=item.get("address"),
        distance_m=round(origin.distance_m(point)) if origin and point else None,
        price=price,
        attributes=attrs,
        facts=facts,
        availability=Availability.UNKNOWN,
        booking=BookingMode.HANDOFF,
        links=[Link(kind=LinkKind.WEBSITE, url=url, label=attribution)],
        provenance=Provenance(
            provider=provider,
            freshness=freshness,
            source_url=url,
            attribution=attribution,
            external_id=str(item.get("id") or url),
            retrieved_at=utcnow(),
        ),
    )


def listing_numeric_ok(result: Result, intent: SearchIntent) -> dict[str, Tri]:
    """Evaluate numeric listing requirements (bedrooms, rooms, area) as tri-state."""
    out: dict[str, Tri] = {}
    for key, fact in (("bedrooms.min", "bedrooms"), ("rooms.min", "rooms"), ("area.min", "area_m2")):
        c = intent.requirement(key) or next((p for p in intent.preferences if p.key == key), None)
        if c is None:
            continue
        v = result.facts.get(fact)
        out[key] = Tri.UNKNOWN if not isinstance(v, int | float) else Tri.YES if v >= float(c.value) else Tri.NO
    return out


def published_at(result: Result) -> datetime | None:
    v = result.facts.get("published_at")
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


class ListingFeedProvider(Provider):
    access = AccessLevel.PARTNER

    def __init__(self, cfg: FeedConfig, http: ProviderHttp | None = None, ttl_s: float = 900) -> None:
        self.cfg = cfg
        self.name = f"feed:{cfg.name}"
        self.display_name = cfg.attribution
        self.attribution = cfg.attribution
        self.capabilities = (
            Capability(
                cfg.domain,
                countries=cfg.countries,
                monitoring_allowed=True,
                min_monitor_interval_s=1800,
                cost_units=0.2,
            ),
        )
        self.http = http or ProviderHttp(self.name, rate_per_s=1, burst=1, max_retries=1)
        self.ttl_s = ttl_s
        self._cache: tuple[float, list[dict[str, Any]]] | None = None

    async def _items(self) -> list[dict[str, Any]]:
        if self._cache and time.monotonic() - self._cache[0] < self.ttl_s:
            return self._cache[1]
        data = await self.http.get_json(self.cfg.url)
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        self._cache = (time.monotonic(), [i for i in items if isinstance(i, dict)])
        return self._cache[1]

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        ref = intent.location
        origin = ref.place.point if ref and ref.place else None
        radius = (ref.radius_m if ref else None) or 15_000
        out = []
        for item in await self._items():
            r = listing_to_result(item, self.name, self.cfg.attribution, origin)
            if r is None or r.domain is not intent.domain:
                continue
            if origin and r.point and origin.distance_m(r.point) > radius:
                continue
            r.attributes.update(listing_numeric_ok(r, intent))
            out.append(r)
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
