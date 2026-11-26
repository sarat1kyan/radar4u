"""Place resolution: text -> canonical Place.

Order: coordinates literal -> IATA code -> built-in gazetteer (all scripts and
aliases) -> Nominatim -> Photon. Online lookups are cached and rate limited to
Nominatim's policy of 1 request per second for the whole application; we never
use Nominatim for type-ahead.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from babel.core import get_global

from radar.config import get_settings
from radar.core.errors import ProviderError
from radar.domain.common import GeoPoint, LocationRef, Place, PlaceKind
from radar.geo.gazetteer_data import GAZETTEER
from radar.providers.http import ProviderHttp

_COORD_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_IATA_RE = re.compile(r"^[A-Z]{3}$")


def normalize(text: str) -> str:
    """Case- and accent-insensitive key. Non-Latin scripts keep their letters."""
    t = unicodedata.normalize("NFKD", text.casefold())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("ё", "е").replace("’", "'")
    t = re.sub(r"[\s\-_.]+", " ", t).strip()
    return t


@dataclass(frozen=True, slots=True)
class GazetteerEntry:
    id: str
    name: str
    country: str
    lat: float
    lon: float
    tz: str
    kind: str
    iata: tuple[str, ...]
    aliases: tuple[str, ...]

    def to_place(self) -> Place:
        return Place(
            canonical_id=f"gz:{self.id}",
            name=self.name,
            display_name=f"{self.name}, {self.country}",
            kind=PlaceKind.REGION if self.kind == "region" else PlaceKind.CITY,
            country_code=self.country,
            point=GeoPoint(lat=self.lat, lon=self.lon),
            timezone=self.tz,
            iata=self.iata[0] if self.iata else None,
            source="gazetteer",
        )


class Gazetteer:
    def __init__(self) -> None:
        self.entries = [GazetteerEntry(*row) for row in GAZETTEER]
        self._by_alias: dict[str, GazetteerEntry] = {}
        self._by_iata: dict[str, GazetteerEntry] = {}
        for e in self.entries:
            for alias in (e.name, *e.aliases):
                self._by_alias.setdefault(normalize(alias), e)
            for code in e.iata:
                self._by_iata.setdefault(code, e)
        # Longest aliases first so "frankfurt am main" wins over "frankfurt".
        self._aliases_sorted = sorted(self._by_alias, key=len, reverse=True)

    def lookup(self, text: str) -> GazetteerEntry | None:
        return self._by_alias.get(normalize(text))

    def by_iata(self, code: str) -> GazetteerEntry | None:
        return self._by_iata.get(code.upper())

    def by_id(self, gid: str) -> GazetteerEntry | None:
        gid = gid.removeprefix("gz:")
        return next((e for e in self.entries if e.id == gid), None)

    def find_in_text(self, text: str) -> list[tuple[int, int, GazetteerEntry]]:
        """All non-overlapping alias mentions in free text, as (start, end, entry)."""
        norm = normalize(text)
        taken: list[tuple[int, int]] = []
        found: list[tuple[int, int, GazetteerEntry]] = []
        for alias in self._aliases_sorted:
            if len(alias) < 3:
                continue
            for m in re.finditer(rf"(?<!\w){re.escape(alias)}\w{{0,3}}(?!\w)", norm):
                s, e = m.span()
                # Allow short inflection suffixes (Берлине, Парижа, Երևանում is longer; handled by stem).
                if any(not (e <= ts or s >= te) for ts, te in taken):
                    continue
                taken.append((s, e))
                found.append((s, e, self._by_alias[alias]))
        found.sort(key=lambda x: x[0])
        return found

    def nearest(self, point: GeoPoint, country: str | None = None) -> GazetteerEntry | None:
        pool = [e for e in self.entries if country is None or e.country == country]
        if not pool:
            return None
        return min(pool, key=lambda e: point.distance_m(GeoPoint(lat=e.lat, lon=e.lon)))


GAZ = Gazetteer()


def timezone_for(point: GeoPoint, country: str | None) -> str | None:
    """Best-effort IANA zone without a tz shapefile.

    Single-zone countries are exact. Multi-zone countries use the nearest
    gazetteer city in that country, else CLDR's first zone for the territory.
    """
    if not country:
        near = GAZ.nearest(point)
        return near.tz if near and point.distance_m(GeoPoint(lat=near.lat, lon=near.lon)) < 150_000 else None
    zones: list[str] = list(get_global("territory_zones").get(country.upper(), []))
    if len(zones) == 1:
        return zones[0]
    near = GAZ.nearest(point, country.upper())
    if near is not None:
        return near.tz
    return zones[0] if zones else None


class _TTLCache:
    def __init__(self, maxsize: int = 2048, ttl_s: float = 7 * 86400) -> None:
        self.maxsize, self.ttl = maxsize, ttl_s
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        hit = self._d.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.monotonic() - ts > self.ttl:
            self._d.pop(key, None)
            return None
        self._d.move_to_end(key)
        return val

    def set(self, key: str, val: Any) -> None:
        self._d[key] = (time.monotonic(), val)
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)


_NOMINATIM_KIND = {
    "city": PlaceKind.CITY,
    "town": PlaceKind.CITY,
    "village": PlaceKind.CITY,
    "municipality": PlaceKind.CITY,
    "state": PlaceKind.REGION,
    "region": PlaceKind.REGION,
    "province": PlaceKind.REGION,
    "country": PlaceKind.COUNTRY,
    "suburb": PlaceKind.DISTRICT,
    "city_district": PlaceKind.DISTRICT,
    "neighbourhood": PlaceKind.DISTRICT,
    "quarter": PlaceKind.DISTRICT,
    "station": PlaceKind.STATION,
    "aerodrome": PlaceKind.AIRPORT,
}


class GeoResolver:
    def __init__(self, http_nominatim: ProviderHttp | None = None, http_photon: ProviderHttp | None = None):
        # Nominatim: hard 1 rps across the app, no burst.
        self.nominatim = http_nominatim or ProviderHttp("nominatim", rate_per_s=1.0, burst=1, max_retries=1)
        self.photon = http_photon or ProviderHttp("photon", rate_per_s=2.0, burst=2, max_retries=1)
        self.cache = _TTLCache()

    async def resolve(
        self, text: str, *, language: str = "en", country_bias: str | None = None, online: bool = True
    ) -> LocationRef:
        ref = LocationRef(text=text)
        raw = text.strip()
        if not raw:
            return ref

        if m := _COORD_RE.match(raw):
            lat, lon = float(m.group(1)), float(m.group(2))
            point = GeoPoint(lat=lat, lon=lon)
            cc = None
            near = GAZ.nearest(point)
            if near and point.distance_m(GeoPoint(lat=near.lat, lon=near.lon)) < 60_000:
                cc = near.country
            ref.place = Place(
                canonical_id=f"geo:{lat:.5f},{lon:.5f}",
                name=f"{lat:.4f}, {lon:.4f}",
                kind=PlaceKind.COORDINATES,
                country_code=cc,
                point=point,
                timezone=timezone_for(point, cc),
                source="coordinates",
            )
            return ref

        if _IATA_RE.match(raw) and (e := GAZ.by_iata(raw)):
            ref.place = e.to_place()
            ref.place.iata = raw
            return ref

        if e := GAZ.lookup(raw):
            ref.place = e.to_place()
            return ref

        if not online:
            return ref

        key = f"{normalize(raw)}|{language}|{country_bias or ''}"
        cached = self.cache.get(key)
        if cached is not None:
            places: list[Place] = cached
        else:
            places = await self._online(raw, language, country_bias)
            self.cache.set(key, places)
        if places:
            ref.place = places[0]
            ref.alternatives = _ambiguous_alternatives(places)
        return ref

    async def reverse(self, lat: float, lon: float, *, language: str = "en") -> Place:
        """Name a shared location ("Kentron, Yerevan") instead of showing raw coordinates."""
        point = GeoPoint(lat=lat, lon=lon)
        near = GAZ.nearest(point)
        cc = near.country if near and point.distance_m(GeoPoint(lat=near.lat, lon=near.lon)) < 60_000 else None
        name = f"{lat:.4f}, {lon:.4f}"
        key = f"rev|{lat:.3f}|{lon:.3f}|{language}"
        cached = self.cache.get(key)
        if cached is not None:
            name, cc = cached
        else:
            try:
                data = await self.nominatim.get_json(
                    f"{get_settings().nominatim_url}/reverse",
                    params={
                        "lat": f"{lat:.5f}",
                        "lon": f"{lon:.5f}",
                        "format": "jsonv2",
                        "zoom": 14,
                        "accept-language": language,
                    },
                )
                addr = (data or {}).get("address") or {}
                district = next((addr[k] for k in ("suburb", "city_district", "neighbourhood") if addr.get(k)), None)
                town = addr.get("city") or addr.get("town") or addr.get("village")
                label = ", ".join(p for p in (district, town) if p)
                if label:
                    name = label
                cc = (addr.get("country_code") or "").upper() or cc
                self.cache.set(key, (name, cc))
            except ProviderError:
                pass
        return Place(
            canonical_id=f"geo:{lat:.5f},{lon:.5f}",
            name=name,
            kind=PlaceKind.COORDINATES,
            country_code=cc,
            point=point,
            timezone=timezone_for(point, cc),
            source="location",
        )

    async def _online(self, text: str, language: str, country_bias: str | None) -> list[Place]:
        try:
            return await self._nominatim(text, language, country_bias)
        except ProviderError:
            pass
        try:
            return await self._photon(text, language)
        except ProviderError:
            return []

    async def _nominatim(self, text: str, language: str, country_bias: str | None) -> list[Place]:
        s = get_settings()
        params: dict[str, Any] = {
            "q": text,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
            "accept-language": language,
        }
        data = await self.nominatim.get_json(f"{s.nominatim_url}/search", params=params)
        out: list[Place] = []
        for item in data if isinstance(data, list) else []:
            try:
                point = GeoPoint(lat=float(item["lat"]), lon=float(item["lon"]))
            except (KeyError, ValueError, TypeError):
                continue
            addr = item.get("address") or {}
            cc = (addr.get("country_code") or "").upper() or None
            bb = item.get("boundingbox")
            bbox = None
            if isinstance(bb, list) and len(bb) == 4:
                try:
                    bbox = (float(bb[0]), float(bb[2]), float(bb[1]), float(bb[3]))
                except ValueError:
                    bbox = None
            kind = _NOMINATIM_KIND.get(str(item.get("addresstype") or item.get("type")), PlaceKind.POI)
            out.append(
                Place(
                    canonical_id=f"osm:{str(item.get('osm_type', 'x'))[:1].upper()}{item.get('osm_id')}",
                    name=str(item.get("name") or text),
                    display_name=item.get("display_name"),
                    kind=kind,
                    country_code=cc,
                    point=point,
                    bbox=bbox,
                    timezone=timezone_for(point, cc),
                    source="nominatim",
                )
            )
        if country_bias:
            out.sort(key=lambda p: 0 if p.country_code == country_bias.upper() else 1)
        return out

    async def _photon(self, text: str, language: str) -> list[Place]:
        s = get_settings()
        lang = language if language in {"en", "de", "fr", "it"} else "default"
        data = await self.photon.get_json(f"{s.photon_url}/api", params={"q": text, "limit": 5, "lang": lang})
        out: list[Place] = []
        for f in (data or {}).get("features", []):
            try:
                lon, lat = f["geometry"]["coordinates"][:2]
                point = GeoPoint(lat=float(lat), lon=float(lon))
            except (KeyError, ValueError, TypeError):
                continue
            p = f.get("properties", {})
            cc = (p.get("countrycode") or "").upper() or None
            out.append(
                Place(
                    canonical_id=f"osm:{str(p.get('osm_type', 'x'))[:1].upper()}{p.get('osm_id')}",
                    name=str(p.get("name") or text),
                    display_name=", ".join(str(x) for x in (p.get("name"), p.get("country")) if x),
                    kind=_NOMINATIM_KIND.get(str(p.get("osm_value")), PlaceKind.POI),
                    country_code=cc,
                    point=point,
                    timezone=timezone_for(point, cc),
                    source="photon",
                )
            )
        return out

    async def aclose(self) -> None:
        await self.nominatim.aclose()
        await self.photon.aclose()


def _ambiguous_alternatives(places: list[Place]) -> list[Place]:
    """Same-named settlements in different countries are worth asking about."""
    top = places[0]
    alts = []
    for p in places[1:]:
        if p.kind in (PlaceKind.CITY, PlaceKind.REGION) and p.country_code != top.country_code:
            alts.append(p)
    return alts[:3]
