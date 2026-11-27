"""Venue discovery from OpenStreetMap via the Overpass API.

What it gives: names, coordinates, address, cuisine, opening hours, website,
phone and community-maintained amenity tags. What it does not give: ratings,
prices, menus, table or appointment availability. Results are DIRECTORY
freshness and availability UNKNOWN by construction.

Policy: the public Overpass instances are for light, non-commercial use. A
commercial deployment must point RADAR_OVERPASS_URLS at its own instance.
Data is (c) OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import OrderedDict
from typing import Any

from radar.config import get_settings
from radar.core.errors import ProviderError
from radar.core.kvcache import KVCache
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.domain.common import Domain, GeoPoint
from radar.domain.intents import SearchIntent
from radar.domain.results import BookingMode, Link, LinkKind, Result
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

# category token -> Overpass tag selectors (each selector becomes one nwr clause)
SELECTORS: dict[str, list[str]] = {
    # dining
    "dining:*": ['["amenity"~"^(restaurant|cafe|bar|pub|biergarten|fast_food)$"]'],
    "place:cafe": ['["amenity"="cafe"]'],
    "place:bar": ['["amenity"="bar"]'],
    "place:pub": ['["amenity"="pub"]'],
    "place:bakery": ['["shop"="bakery"]'],
    "place:brunch": [
        '["amenity"~"^(cafe|restaurant)$"]["brunch"="yes"]',
        '["amenity"~"^(cafe|restaurant)$"]["cuisine"~"breakfast|brunch",i]',
    ],
    # wellness
    "place:spa": [
        '["leisure"="spa"]',
        '["amenity"="spa"]',
        '["shop"="beauty"]["beauty"~"spa",i]',
        '["amenity"="public_bath"]',
    ],
    "place:massage": ['["shop"="massage"]', '["leisure"="spa"]', '["amenity"="spa"]'],
    "place:sauna": ['["leisure"="sauna"]', '["amenity"="public_bath"]'],
    "place:beauty": ['["shop"="beauty"]', '["shop"="cosmetics"]["beauty"]'],
    "place:hairdresser": ['["shop"="hairdresser"]'],
    "place:fitness": ['["leisure"="fitness_centre"]', '["sport"="yoga"]'],
    "place:coworking": ['["amenity"="coworking_space"]', '["office"="coworking"]'],
    "place:pet_grooming": ['["shop"="pet_grooming"]'],
    "place:pet_boarding": ['["amenity"="animal_boarding"]'],
    "place:photographer": ['["craft"="photographer"]', '["shop"="photo"]'],
    "place:repair": ['["craft"~"^(electronics_repair|shoemaker|watchmaker|tailor)$"]', '["shop"="repair"]'],
    "wellness:*": ['["leisure"~"^(spa|sauna|fitness_centre)$"]', '["shop"~"^(massage|beauty|hairdresser)$"]'],
    # event venues (not event schedules)
    "event:museum": ['["tourism"="museum"]'],
    "event:exhibition": ['["tourism"="gallery"]', '["amenity"="exhibition_centre"]'],
    "event:theatre": ['["amenity"="theatre"]'],
    "event:cinema": ['["amenity"="cinema"]'],
    "event:concert": ['["amenity"~"^(music_venue|concert_hall|nightclub)$"]'],
    "event:escape_room": ['["leisure"="escape_game"]'],
    "event:karting": ['["sport"="karting"]'],
    "event:amusement_park": ['["tourism"="theme_park"]'],
    "event:vr": ['["leisure"="amusement_arcade"]["name"~"VR|virtual",i]'],
    "event:ski": ['["landuse"="winter_sports"]', '["sport"="skiing"]["leisure"]'],
    "event:sports": ['["leisure"="stadium"]'],
    "event:workshop": ['["amenity"="arts_centre"]'],
    # stays (directory only: no prices or room availability)
    "property:hotel": ['["tourism"="hotel"]'],
    "property:hostel": ['["tourism"="hostel"]'],
    "property:guesthouse": ['["tourism"="guest_house"]'],
    "property:apartment": ['["tourism"="apartment"]'],
    "property:aparthotel": ['["tourism"="apartment"]', '["tourism"="hotel"]["hotel"="aparthotel"]'],
    "stay:*": ['["tourism"~"^(hotel|hostel|guest_house|apartment|motel)$"]'],
    "event:sights": [
        '["tourism"~"^(attraction|viewpoint|museum|gallery)$"]',
        '["historic"~"^(monument|castle|memorial|ruins|archaeological_site|fort)$"]',
    ],
    "event:*": ['["tourism"~"^(museum|gallery|theme_park)$"]', '["amenity"~"^(theatre|cinema|arts_centre)$"]'],
    # travel essentials (many are unnamed in OSM; see NAME_OPTIONAL)
    "place:pharmacy": ['["amenity"="pharmacy"]', '["healthcare"="pharmacy"]'],
    "place:atm": ['["amenity"="atm"]', '["amenity"="bank"]["atm"="yes"]'],
    "place:exchange": ['["amenity"="bureau_de_change"]'],
    "place:supermarket": ['["shop"~"^(supermarket|convenience)$"]'],
    "place:hospital": ['["amenity"~"^(hospital|clinic)$"]', '["healthcare"~"^(hospital|clinic)$"]'],
    "place:police": ['["amenity"="police"]'],
    "place:toilets": ['["amenity"="toilets"]'],
    "place:fuel": ['["amenity"="fuel"]', '["amenity"="charging_station"]'],
    "place:embassy": ['["office"="diplomatic"]', '["amenity"="embassy"]', '["diplomatic"="embassy"]'],
    "place:sim": ['["shop"="mobile_phone"]', '["shop"="telecommunication"]'],
    "place:laundry": ['["shop"~"^(laundry|dry_cleaning)$"]'],
    "essentials:*": ['["amenity"~"^(pharmacy|atm|bureau_de_change)$"]', '["shop"="supermarket"]'],
}

# Selectors whose objects are often unnamed but still useful (an ATM, a public toilet).
NAME_OPTIONAL = frozenset({"place:atm", "place:toilets", "place:exchange", "place:fuel", "place:pharmacy"})

KIND_FALLBACK = {
    "atm": "ATM",
    "bank": "Bank",
    "toilets": "Toilets",
    "bureau_de_change": "Currency exchange",
    "fuel": "Fuel",
    "charging_station": "Charging station",
    "pharmacy": "Pharmacy",
}

ATTRIBUTE_TAGS: dict[str, tuple[str, ...]] = {
    "outdoor_seating": ("outdoor_seating",),
    "indoor_seating": ("indoor_seating",),
    "takeaway": ("takeaway",),
    "delivery": ("delivery",),
    "wheelchair": ("wheelchair",),
    "diet.vegan": ("diet:vegan",),
    "diet.vegetarian": ("diet:vegetarian",),
    "diet.halal": ("diet:halal",),
    "diet.kosher": ("diet:kosher",),
    "diet.gluten_free": ("diet:gluten_free",),
    "air_conditioning": ("air_conditioning",),
    "pet_friendly": ("dog",),
    "sauna": ("sauna",),
    "pool": ("swimming_pool",),
    "breakfast": ("breakfast",),
}


def _cuisine_filter(cats: list[str]) -> str:
    names = [c.split(":", 1)[1] for c in cats if c.startswith("cuisine:")]
    if not names:
        return ""
    alt = "|".join(sorted(set(names)))
    return f'["cuisine"~"{alt}",i]'


def build_query(intent: SearchIntent, lat: float, lon: float, radius_m: int, limit: int) -> str:
    cats = intent.categories
    keys = [c for c in cats if c in SELECTORS]
    if not keys:
        keys = {
            Domain.DINING: ["dining:*"],
            Domain.WELLNESS: ["wellness:*"],
            Domain.EVENT: ["event:*"],
            Domain.STAY: ["stay:*"],
            Domain.ESSENTIALS: ["essentials:*"],
        }.get(intent.domain, ["dining:*"])
    cuisine = _cuisine_filter(cats) if intent.domain is Domain.DINING else ""
    clauses = []
    for key in keys:
        for sel in SELECTORS[key]:
            named = "" if key in NAME_OPTIONAL else '["name"]'
            clauses.append(f"  nw{sel}{cuisine}{named};")
    if intent.domain is Domain.DINING and cuisine and not any(k.startswith("place:") for k in keys):
        clauses = [f'  nw["amenity"~"^(restaurant|cafe|fast_food)$"]{cuisine}["name"];']
    body = "\n".join(dict.fromkeys(clauses))
    # A bounding box is several times cheaper for Overpass than around:; results are cut to the
    # circle afterwards using the real distance.
    s_, w_, n_, e_ = bbox_around(lat, lon, radius_m)
    return f"[out:json][timeout:15][bbox:{s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f}];\n(\n{body}\n);\nout center tags {limit};"


def bbox_around(lat: float, lon: float, radius_m: int) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _name(tags: dict[str, str], lang: str) -> str:
    return tags.get(f"name:{lang}") or tags.get("name") or tags.get("name:en") or "?"


def _tri_reservation(v: str | None) -> Tri:
    if v is None:
        return Tri.UNKNOWN
    return Tri.NO if v == "no" else Tri.YES if v in ("yes", "required", "recommended") else Tri.UNKNOWN


def _wifi(tags: dict[str, str]) -> Tri:
    v = tags.get("internet_access")
    if v is None:
        return Tri.UNKNOWN
    if v in ("no",):
        return Tri.NO
    return Tri.YES if v in ("wlan", "yes", "wifi") else Tri.UNKNOWN


def commons_file_url(name: str, width: int = 960) -> str:
    """Resized image from Wikimedia Commons. Commons serves fixed widths (250, 330, 500, 960, 1280...)."""
    from urllib.parse import quote

    file = name.removeprefix("File:").strip().replace(" ", "_")
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(file)}?width={width}"


def photo_url(tags: dict[str, str]) -> str | None:
    commons = tags.get("wikimedia_commons", "")
    if commons.startswith("File:"):
        return commons_file_url(commons)
    img = tags.get("image", "")
    if img.startswith("File:"):
        return commons_file_url(img)
    if img.startswith("https://") and img.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
        return img
    return None


def element_to_result(el: dict[str, Any], intent: SearchIntent, lang: str, origin: GeoPoint) -> Result | None:
    tags: dict[str, str] = el.get("tags") or {}
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    kind = tags.get("amenity") or tags.get("shop") or tags.get("leisure") or tags.get("tourism") or tags.get("craft")
    if "name" not in tags:
        fallback = tags.get("brand") or tags.get("operator") or KIND_FALLBACK.get(kind or "")
        if intent.domain is not Domain.ESSENTIALS or not fallback:
            return None
        tags = {**tags, "name": fallback}
    point = GeoPoint(lat=float(lat), lon=float(lon))
    osm_ref = f"{el.get('type', 'node')}/{el.get('id')}"
    attrs = {k: Tri.from_osm(next((tags[t] for t in ts if t in tags), None)) for k, ts in ATTRIBUTE_TAGS.items()}
    attrs["wifi"] = _wifi(tags)
    attrs["reservations"] = _tri_reservation(tags.get("reservation"))
    attrs["parking"] = Tri.UNKNOWN  # OSM venue tags rarely state customer parking reliably
    if tags.get("diet:vegan") == "only":
        attrs["diet.vegetarian"] = Tri.YES

    addr = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x) or None
    if addr and tags.get("addr:city"):
        addr = f"{addr}, {tags['addr:city']}"

    links: list[Link] = []
    website = tags.get("website") or tags.get("contact:website")
    if website and website.startswith(("http://", "https://")):
        links.append(Link(kind=LinkKind.WEBSITE, url=website))
    if (menu := tags.get("website:menu")) and menu.startswith("http"):
        links.append(Link(kind=LinkKind.MENU, url=menu))
    phone = tags.get("phone") or tags.get("contact:phone")
    if phone:
        links.append(Link(kind=LinkKind.PHONE, url=f"tel:{phone.split(';')[0].replace(' ', '')}"))
    links.append(Link(kind=LinkKind.SOURCE, url=f"https://www.openstreetmap.org/{osm_ref}", label="OpenStreetMap"))

    cuisines = [c.strip() for c in tags.get("cuisine", "").split(";") if c.strip()]
    if tags.get("office") == "diplomatic" or tags.get("healthcare"):
        kind = kind or tags.get("office") or tags.get("healthcare")
    facts: dict[str, Any] = {
        "kind": kind,
        "cuisine": cuisines,
        "opening_hours": tags.get("opening_hours"),
        "osm": osm_ref,
    }
    if (stars := tags.get("stars")) and stars[:1].isdigit():
        facts["stars"] = int(stars[0])
    if (rooms := tags.get("rooms")) and rooms.isdigit():
        facts["rooms"] = int(rooms)
    if photo := photo_url(tags):
        facts["photo"] = photo
    if tags.get("wikidata", "").startswith("Q"):
        facts["wikidata"] = tags["wikidata"]
    if tags.get("description"):
        # Untrusted free text: stored for display only.
        facts["description"] = tags["description"][:300]
    return Result(
        domain=intent.domain,
        title=_name(tags, lang),
        subtitle=", ".join(cuisines[:3]) if cuisines else kind,
        categories=[f"cuisine:{c}" for c in cuisines] + ([f"kind:{kind}"] if kind else []),
        point=point,
        address=addr,
        distance_m=round(origin.distance_m(point)),
        attributes=attrs,
        facts=facts,
        booking=BookingMode.HANDOFF if website else BookingMode.CONTACT if phone else BookingMode.NONE,
        links=links,
        provenance=Provenance(
            provider="osm",
            freshness=Freshness.DIRECTORY,
            source_url=f"https://www.openstreetmap.org/{osm_ref}",
            attribution="(c) OpenStreetMap contributors, ODbL",
            external_id=osm_ref,
            retrieved_at=utcnow(),
        ),
    )


class OsmPlacesProvider(Provider):
    name = "osm"
    display_name = "OpenStreetMap (Overpass)"
    access = AccessLevel.PUBLIC
    # The public instances ask commercial users to self-host; with a self-hosted
    # RADAR_OVERPASS_URLS this flag can be overridden in the registry.
    commercial_use_allowed = False
    docs_url = "https://wiki.openstreetmap.org/wiki/Overpass_API"
    attribution = "(c) OpenStreetMap contributors, ODbL"
    capabilities = (
        Capability(Domain.DINING, monitoring_allowed=True, min_monitor_interval_s=86400, cost_units=0.5),
        Capability(Domain.WELLNESS, monitoring_allowed=True, min_monitor_interval_s=86400, cost_units=0.5),
        Capability(Domain.ESSENTIALS, monitoring_allowed=False, cost_units=0.5),
        Capability(Domain.EVENT, monitoring_allowed=False, cost_units=0.5),
        Capability(Domain.STAY, monitoring_allowed=False, cost_units=0.5),
    )

    def __init__(
        self, http: ProviderHttp | None = None, cache_ttl_s: float = 3600, store: KVCache | None = None
    ) -> None:
        # Overpass etiquette: one request at a time, no bursts. One client per mirror so a dead
        # mirror trips its own breaker without hiding the health of the others.
        self._shared = http
        self._mirrors: dict[str, ProviderHttp] = {}
        self._preferred: str | None = None
        self.store = store or KVCache()
        self.wikidata = ProviderHttp("wikidata", rate_per_s=2, burst=2, max_retries=0, timeout_s=8)
        self.disk_ttl_s = 24 * 3600
        self.hedge_after_s = 2.5
        self.cache_ttl_s = cache_ttl_s
        self._cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._coverage: dict[str, bool] = {}

    async def _covers(self, url: str, at: GeoPoint) -> bool | None:
        """Does this mirror hold data around `at`? Some public mirrors carry a regional extract only and
        give partial, misleading answers near its edge (a Turkish airport, but not the Armenian one 10 km
        away). One tiny probe per mirror and 1-degree cell, remembered for a week. None means unknown."""
        cell = f"{round(at.lat)}:{round(at.lon)}"
        key = f"cov:{url}:{cell}"
        if key in self._coverage:
            return self._coverage[key]
        stored = await self.store.get(key, 7 * 86400)
        if isinstance(stored, bool):
            self._coverage[key] = stored
            return stored
        probe = f"[out:json][timeout:8];node(around:5000,{at.lat:.4f},{at.lon:.4f});out ids 1;"
        try:
            found = bool(await asyncio.wait_for(self._one(url, probe), 6))
        except (ProviderError, TimeoutError):
            return None
        self._coverage[key] = found
        await self.store.set(key, found)
        return found

    async def _one(self, url: str, query: str) -> list[dict[str, Any]]:
        resp = await self._client(url).request("POST", url, data={"data": query}, retry_on_post=False)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(self.name, "invalid JSON from upstream") from exc
        elements = payload.get("elements", [])
        remark = str(payload.get("remark") or "")
        if not elements and ("error" in remark.lower() or "timed out" in remark.lower()):
            raise ProviderError(self.name, "Overpass query did not complete", retryable=True)
        return list(elements)

    async def _fetch(self, query: str, at: GeoPoint | None = None) -> list[dict[str, Any]]:
        """Memory cache -> disk cache -> hedged request across mirrors (first good answer wins)."""
        key = hashlib.sha256(query.encode()).hexdigest()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl_s:
            return hit[1]
        stored = await self.store.get(f"osm:{key}", self.disk_ttl_s)
        if isinstance(stored, list):
            self._cache[key] = (time.monotonic(), stored)
            return stored
        urls = [u for u in get_settings().overpass_urls if self._client(u).breaker.allow()]
        if self._preferred in urls:
            urls.remove(self._preferred)
            urls.insert(0, self._preferred)
        if not urls:
            raise ProviderError(self.name, "all Overpass mirrors are cooling down", code="circuit_open")
        if at is not None and len(urls) > 1:
            cover = await asyncio.gather(*(self._covers(u, at) for u in urls))
            # Known-blind mirrors are dropped; if every probe says "nothing here" (open sea, desert)
            # keep the list as it was rather than refusing to answer.
            kept = [u for u, c in zip(urls, cover, strict=True) if c is not False]
            urls = kept or urls
        last: BaseException | None = None
        empty_ok = False
        pending: set[asyncio.Task[list[dict[str, Any]]]] = set()
        url_of: dict[asyncio.Task[list[dict[str, Any]]], str] = {}
        queue = list(urls)
        try:
            while queue or pending:
                if queue:
                    url = queue.pop(0)
                    task = asyncio.create_task(self._one(url, query))
                    pending.add(task)
                    url_of[task] = url
                # Give the current mirror a head start; start the next one if it is slow.
                done, pending = await asyncio.wait(
                    pending, timeout=self.hedge_after_s if queue else None, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    if t.exception() is None:
                        elements = t.result()
                        if not elements and (queue or pending):
                            # Some public mirrors carry only a regional extract and answer "nothing"
                            # for other countries. An empty answer is only trusted once every mirror agrees.
                            empty_ok = True
                            continue
                        self._preferred = url_of[t]
                        self._cache[key] = (time.monotonic(), elements)
                        while len(self._cache) > 256:
                            self._cache.popitem(last=False)
                        await self.store.set(f"osm:{key}", elements)
                        return elements
                    last = t.exception()
        finally:
            for t in pending:
                t.cancel()
        if empty_ok:
            return []
        if isinstance(last, ProviderError):
            raise last
        raise ProviderError(self.name, "no Overpass mirror answered")

    async def nearest(self, point: GeoPoint, selector: str, radius_m: int, limit: int = 30) -> list[dict[str, Any]]:
        """Raw elements matching one Overpass selector around a point, nearest first.

        Used by travel mode to find the closest airport or station without a full search.
        """
        s_, w_, n_, e_ = bbox_around(point.lat, point.lon, radius_m)
        query = (
            f"[out:json][timeout:15][bbox:{s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f}];\n"
            f"(\n  nwr{selector};\n);\nout center tags {limit};"
        )
        out: list[dict[str, Any]] = []
        for el in await self._fetch(query, point):
            lat = el.get("lat") or (el.get("center") or {}).get("lat")
            lon = el.get("lon") or (el.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            pt = GeoPoint(lat=float(lat), lon=float(lon))
            out.append({"tags": el.get("tags") or {}, "point": pt, "distance_m": point.distance_m(pt)})
        out.sort(key=lambda x: x["distance_m"])
        return out

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        ref = intent.location or intent.destination
        if ref is None or ref.place is None:
            return []
        center = ref.place.point
        radius = ref.radius_m or (2500 if ref.place.kind.value in ("district", "poi", "address") else 5000)
        radius = max(300, min(radius, 25_000))
        limit = min(150, ctx.max_results * 4)
        elements = await self._fetch(build_query(intent, center.lat, center.lon, radius, limit=limit), center)
        if len(elements) < 6 and not ref.radius_m and radius < 25_000:
            # Sparse area or rare category: widen once instead of returning almost nothing.
            radius = min(25_000, radius * 3)
            elements = await self._fetch(build_query(intent, center.lat, center.lon, radius, limit=limit), center)
        out: list[Result] = []
        seen: set[str] = set()
        for el in elements:
            r = element_to_result(el, intent, ctx.locale, center)
            if r is None or r.id in seen:
                continue
            if r.distance_m is not None and r.distance_m > radius * 1.05:
                continue
            seen.add(r.id)
            out.append(r)
        out.sort(key=lambda r: r.distance_m if r.distance_m is not None else 1e12)
        out = out[: max(ctx.max_results * 2, 40)]
        await self._enrich_photos(out[:30])
        return out

    async def _enrich_photos(self, results: list[Result]) -> None:
        """Wikidata image (P18) for venues that link a Wikidata item but have no photo tag."""
        want = {r.facts["wikidata"]: r for r in results if r.facts.get("wikidata") and not r.facts.get("photo")}
        if not want:
            return
        files: dict[str, str | None] = {}
        missing = []
        for qid in want:
            cached = await self.store.get(f"wd:p18:{qid}", 30 * 86400)
            if cached is not None:
                files[qid] = cached or None
            else:
                missing.append(qid)
        if missing:
            try:
                data = await self.wikidata.get_json(
                    "https://www.wikidata.org/w/api.php",
                    params={
                        "action": "wbgetentities",
                        "ids": "|".join(missing[:50]),
                        "props": "claims",
                        "format": "json",
                    },
                )
            except ProviderError:
                data = {}
            for qid, ent in ((data or {}).get("entities") or {}).items():
                claims = (ent or {}).get("claims") or {}
                name = None
                try:
                    name = claims["P18"][0]["mainsnak"]["datavalue"]["value"]
                except (KeyError, IndexError, TypeError):
                    name = None
                files[qid] = name
                await self.store.set(f"wd:p18:{qid}", name or "")
        for qid, r in want.items():
            if files.get(qid):
                r.facts["photo"] = commons_file_url(str(files[qid]))

    def _client(self, url: str) -> ProviderHttp:
        if self._shared is not None:
            return self._shared
        if url not in self._mirrors:
            mirror = ProviderHttp(
                f"osm:{url.split('/')[2]}", rate_per_s=1, burst=2, max_retries=0, timeout_s=22, connect_timeout_s=4
            )
            # A mirror that fails twice in a row is skipped for five minutes.
            mirror.breaker.failure_threshold = 2
            mirror.breaker.reset_after_s = 300
            self._mirrors[url] = mirror
        return self._mirrors[url]

    def health(self) -> dict[str, str]:
        return {name: h.breaker.state.value for name, h in self._mirrors.items()}

    async def aclose(self) -> None:
        await self.wikidata.aclose()
        for h in self._mirrors.values():
            await h.aclose()
        if self._shared is not None:
            await self._shared.aclose()
