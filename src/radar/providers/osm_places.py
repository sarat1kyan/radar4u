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

import hashlib
import time
from collections import OrderedDict
from typing import Any

from radar.config import get_settings
from radar.core.errors import ProviderError
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
    "event:*": ['["tourism"~"^(museum|gallery|theme_park)$"]', '["amenity"~"^(theatre|cinema|arts_centre)$"]'],
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
        }.get(intent.domain, ["dining:*"])
    cuisine = _cuisine_filter(cats) if intent.domain is Domain.DINING else ""
    clauses = []
    for key in keys:
        for sel in SELECTORS[key]:
            clauses.append(f'  nwr{sel}{cuisine}["name"](around:{radius_m},{lat:.6f},{lon:.6f});')
    if intent.domain is Domain.DINING and cuisine and not any(k.startswith("place:") for k in keys):
        clauses = [
            f'  nwr["amenity"~"^(restaurant|cafe|fast_food)$"]{cuisine}["name"](around:{radius_m},{lat:.6f},{lon:.6f});'
        ]
    body = "\n".join(dict.fromkeys(clauses))
    return f"[out:json][timeout:20];\n(\n{body}\n);\nout center tags {limit};"


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
    if lat is None or lon is None or "name" not in tags:
        return None
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
    kind = tags.get("amenity") or tags.get("shop") or tags.get("leisure") or tags.get("tourism") or tags.get("craft")
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
        # Untrusted free text: stored for display only, never fed to language models.
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
        Capability(Domain.EVENT, monitoring_allowed=False, cost_units=0.5),
        Capability(Domain.STAY, monitoring_allowed=False, cost_units=0.5),
    )

    def __init__(self, http: ProviderHttp | None = None, cache_ttl_s: float = 3600) -> None:
        # Overpass etiquette: one request at a time, no bursts. One client per mirror so a dead
        # mirror trips its own breaker without hiding the health of the others.
        self._shared = http
        self._mirrors: dict[str, ProviderHttp] = {}
        self._preferred: str | None = None
        self.cache_ttl_s = cache_ttl_s
        self._cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()

    async def _fetch(self, query: str) -> list[dict[str, Any]]:
        key = hashlib.sha256(query.encode()).hexdigest()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl_s:
            return hit[1]
        last: ProviderError | None = None
        urls = list(get_settings().overpass_urls)
        # Try the mirror that answered last time first; dead mirrors sit behind an open breaker.
        if self._preferred in urls:
            urls.remove(self._preferred)
            urls.insert(0, self._preferred)
        for url in urls:
            http = self._client(url)
            try:
                resp = await http.request("POST", url, data={"data": query}, retry_on_post=True)
                payload = resp.json()
                elements = payload.get("elements", [])
                remark = str(payload.get("remark") or "")
                if not elements and ("error" in remark.lower() or "timed out" in remark.lower()):
                    raise ProviderError(self.name, "Overpass query did not complete", retryable=True)
                self._preferred = url
                self._cache[key] = (time.monotonic(), elements)
                while len(self._cache) > 256:
                    self._cache.popitem(last=False)
                return list(elements)
            except ProviderError as exc:
                last = exc
            except ValueError:
                last = ProviderError(self.name, "invalid JSON from upstream")
        raise last or ProviderError(self.name, "no Overpass endpoint configured")

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        ref = intent.location or intent.destination
        if ref is None or ref.place is None:
            return []
        center = ref.place.point
        radius = ref.radius_m or (2500 if ref.place.kind.value in ("district", "poi", "address") else 5000)
        radius = max(300, min(radius, 25_000))
        query = build_query(intent, center.lat, center.lon, radius, limit=min(120, ctx.max_results * 4))
        elements = await self._fetch(query)
        out: list[Result] = []
        seen: set[str] = set()
        for el in elements:
            r = element_to_result(el, intent, ctx.locale, center)
            if r is None or r.id in seen:
                continue
            seen.add(r.id)
            out.append(r)
        return out

    def _client(self, url: str) -> ProviderHttp:
        if self._shared is not None:
            return self._shared
        if url not in self._mirrors:
            mirror = ProviderHttp(
                f"osm:{url.split('/')[2]}", rate_per_s=0.5, burst=1, max_retries=1, timeout_s=25, connect_timeout_s=4
            )
            # A mirror that fails twice in a row is skipped for five minutes.
            mirror.breaker.failure_threshold = 2
            mirror.breaker.reset_after_s = 300
            self._mirrors[url] = mirror
        return self._mirrors[url]

    def health(self) -> dict[str, str]:
        return {name: h.breaker.state.value for name, h in self._mirrors.items()}

    async def aclose(self) -> None:
        for h in self._mirrors.values():
            await h.aclose()
        if self._shared is not None:
            await self._shared.aclose()
