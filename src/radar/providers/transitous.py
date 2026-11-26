"""Public transport journeys from Transitous (MOTIS 2, open GTFS/GTFS-RT feeds).

Gives real timetabled journeys for rail, coach, bus and ferry across Europe and
beyond, with realtime delays where feeds provide them. It gives no prices and
no seat availability, so every result is TIMETABLE freshness with availability
UNKNOWN and a hand-off to the operator for tickets.

Policy (transitous.org/api): identifying User-Agent required, non-commercial
use only, attribution to transitous.org/sources. Heavy usage must be agreed
with the project. The adapter is disabled when RADAR_COMMERCIAL=true.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

from radar.config import get_settings
from radar.core.errors import ProviderError
from radar.core.provenance import Freshness, Provenance, Tri, utcnow
from radar.core.timeutil import tz
from radar.domain.common import Domain, GeoPoint, Place, PlaceKind
from radar.domain.intents import SearchIntent
from radar.domain.results import Availability, BookingMode, Connection, Leg, Link, LinkKind, Result, Stop
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.http import ProviderHttp

RAIL_MODES = {
    "HIGHSPEED_RAIL",
    "LONG_DISTANCE",
    "NIGHT_RAIL",
    "REGIONAL_RAIL",
    "REGIONAL_FAST_RAIL",
    "RAIL",
    "SUBURBAN",
    "METRO",
    "SUBWAY",
    "TRAM",
}

# Official ticket sites for common operators. Used only when the feed has no agency URL.
OPERATOR_SITES: dict[str, str] = {
    "db ": "https://www.bahn.de",
    "deutsche bahn": "https://www.bahn.de",
    "sncf": "https://www.sncf-connect.com",
    "trenitalia": "https://www.trenitalia.com",
    "italo": "https://www.italotreno.com",
    "öbb": "https://www.oebb.at",
    "oebb": "https://www.oebb.at",
    "sbb": "https://www.sbb.ch",
    "renfe": "https://www.renfe.com",
    "flixbus": "https://www.flixbus.com",
    "flixtrain": "https://www.flixtrain.com",
    "české dráhy": "https://www.cd.cz",
    "pkp intercity": "https://www.intercity.pl",
    "ns ": "https://www.ns.nl",
    "nederlandse spoorwegen": "https://www.ns.nl",
    "eurostar": "https://www.eurostar.com",
    "sncb": "https://www.belgiantrain.be",
    "nmbs": "https://www.belgiantrain.be",
    "cp ": "https://www.cp.pt",
    "comboios de portugal": "https://www.cp.pt",
    "mávstart": "https://www.mavcsoport.hu",
    "regiojet": "https://www.regiojet.com",
    "georgian railway": "https://www.railway.ge",
    "south caucasus railway": "https://www.ukzhd.am",
}

SOURCES_URL = "https://transitous.org/sources/"


def _modes(intent: SearchIntent) -> str:
    if intent.domain is Domain.RAIL:
        return "RAIL"
    cats = set(intent.categories)
    modes: list[str] = []
    if "mode:bus" in cats:
        modes += ["COACH", "BUS"]
    if "mode:ferry" in cats:
        modes.append("FERRY")
    if not modes or "mode:multimodal" in cats:
        return "TRANSIT"
    return ",".join(dict.fromkeys(modes))


def _dt(value: str | None, zone: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(tz(zone)) if zone else dt


def _place(p: dict[str, Any], arrival: bool) -> Stop:
    zone = p.get("tz")
    t = _dt(p.get("arrival") if arrival else p.get("departure"), zone)
    return Stop(
        name=str(p.get("name") or "?"),
        code=p.get("stopId"),
        time=t,
        platform=p.get("track") or p.get("scheduledTrack"),
        point=GeoPoint(lat=float(p["lat"]), lon=float(p["lon"])) if "lat" in p and "lon" in p else None,
    )


def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def operator_link(agency: str | None, agency_url: str | None) -> str | None:
    if agency_url and agency_url.startswith("https://"):
        return agency_url
    if not agency:
        return None
    a = f"{agency.lower()} "
    for key, url in OPERATOR_SITES.items():
        if key in a:
            return url
    return None


def itinerary_to_result(it: dict[str, Any], domain_hint: Domain) -> Result | None:
    raw_legs = [leg for leg in it.get("legs", []) if leg.get("mode") != "WALK"]
    if not raw_legs:
        return None
    legs: list[Leg] = []
    for leg in raw_legs:
        legs.append(
            Leg(
                mode=str(leg.get("mode", "")).lower(),
                carrier=leg.get("agencyName") or None,
                service=leg.get("displayName") or leg.get("tripShortName") or leg.get("routeShortName"),
                origin=_place(leg.get("from", {}), arrival=False),
                destination=_place(leg.get("to", {}), arrival=True),
                duration_min=int(leg.get("duration", 0)) // 60 or None,
                intermediate_stops=len(leg.get("intermediateStops") or []),
                realtime=bool(leg.get("realTime")),
                cancelled=bool(leg.get("cancelled")),
            )
        )
    connections: list[Connection] = []
    for a, b in itertools.pairwise(legs):
        gap = None
        if a.destination.time and b.origin.time:
            gap = int((b.origin.time - a.destination.time).total_seconds() // 60)
        connections.append(
            Connection(
                at=a.destination.name,
                minutes=gap,
                station_change=a.destination.name != b.origin.name,
                overnight=bool(
                    a.destination.time and b.origin.time and a.destination.time.date() != b.origin.time.date()
                ),
            )
        )
    all_rail = all(raw.get("mode") in RAIL_MODES for raw in raw_legs)
    domain = Domain.RAIL if all_rail else Domain.GROUND
    if domain_hint is Domain.RAIL and not all_rail:
        domain = Domain.GROUND

    # MOTIS collapses GTFS "no information" and "not allowed" into the same value, so only a
    # positive statement on every leg counts; everything else stays unknown.
    bikes_ok = all(raw.get("bikesAllowed") is True for raw in raw_legs)
    wheel_ok = all(raw.get("wheelchairAccessible") == "ACCESSIBLE" for raw in raw_legs)
    attrs = {
        "direct": Tri.YES if len(legs) == 1 else Tri.NO,
        "bicycle": Tri.YES if bikes_ok else Tri.UNKNOWN,
        "wheelchair": Tri.YES if wheel_ok else Tri.UNKNOWN,
        "sleeper": Tri.YES if any(raw.get("mode") == "NIGHT_RAIL" for raw in raw_legs) else Tri.UNKNOWN,
    }
    first, last = legs[0], legs[-1]
    links: list[Link] = []
    seen: set[str] = set()
    for raw in raw_legs:
        url = operator_link(raw.get("agencyName"), raw.get("agencyFareUrl") or raw.get("agencyUrl"))
        if url and url not in seen:
            seen.add(url)
            links.append(Link(kind=LinkKind.BOOKING, url=url, label=raw.get("agencyName")))
    links.append(Link(kind=LinkKind.SOURCE, url=SOURCES_URL, label="Transitous"))
    services = " + ".join(x.service or x.mode for x in legs)
    return Result(
        domain=domain,
        title=services,
        subtitle=f"{first.origin.name} -> {last.destination.name}",
        point=first.origin.point,
        starts_at=first.origin.time,
        ends_at=last.destination.time,
        legs=legs,
        connections=connections,
        attributes=attrs,
        availability=Availability.UNKNOWN,
        booking=BookingMode.HANDOFF if len(links) > 1 else BookingMode.NONE,
        links=links,
        facts={
            "transfers": it.get("transfers"),
            "realtime": any(x.realtime for x in legs),
            "cancelled": any(x.cancelled for x in legs),
        },
        provenance=Provenance(
            provider="transitous",
            freshness=Freshness.TIMETABLE,
            source_url=SOURCES_URL,
            attribution="Transitous, open transit data (transitous.org/sources)",
            external_id=f"{first.service}:{first.origin.code}:{_iso(first.origin.time)}",
            retrieved_at=utcnow(),
        ),
    )


class TransitousProvider(Provider):
    name = "transitous"
    display_name = "Transitous"
    access = AccessLevel.PUBLIC
    commercial_use_allowed = False
    docs_url = "https://transitous.org/api/"
    attribution = "Transitous (transitous.org/sources)"
    capabilities = (
        Capability(Domain.RAIL, monitoring_allowed=False, cost_units=1.0),
        Capability(Domain.GROUND, monitoring_allowed=False, cost_units=1.0),
    )

    def __init__(self, http: ProviderHttp | None = None) -> None:
        self.http = http or ProviderHttp("transitous", rate_per_s=1.0, burst=2, max_retries=1, timeout_s=40)
        self._stations: dict[str, str] = {}

    async def station_for(self, place: Place) -> str:
        """Main stop for a city (stop id), so journeys run station to station rather than from the
        city centroid with local transit glued on. Falls back to coordinates."""
        coords = f"{place.point.lat:.6f},{place.point.lon:.6f}"
        if place.kind not in (PlaceKind.CITY, PlaceKind.REGION):
            return coords
        if place.canonical_id in self._stations:
            return self._stations[place.canonical_id]
        result = coords
        try:
            data = await self.http.get_json(
                f"{get_settings().transitous_url}/api/v1/geocode",
                params={"text": place.name, "type": "STOP", "place": coords, "numResults": 5},
            )
        except ProviderError:
            return coords
        best: tuple[float, str] | None = None
        for cand in data if isinstance(data, list) else []:
            try:
                pt = GeoPoint(lat=float(cand["lat"]), lon=float(cand["lon"]))
            except (KeyError, TypeError, ValueError):
                continue
            if cand.get("type") != "STOP" or not cand.get("id") or place.point.distance_m(pt) > 20_000:
                continue
            score = float(cand.get("importance") or 0)
            if best is None or score > best[0]:
                best = (score, str(cand["id"]))
        if best is not None:
            result = best[1]
        self._stations[place.canonical_id] = result
        return result

    async def search(self, intent: SearchIntent, ctx: SearchContext) -> list[Result]:
        o, d = intent.origin, intent.destination
        if not (o and o.place and d and d.place):
            return []
        when = intent.when
        if when and intent.arrive_by and when.end:
            t, arrive_by = when.end, True
        elif when and when.start:
            t, arrive_by = when.start, False
        else:
            t, arrive_by = datetime.now(UTC) + timedelta(minutes=10), False
        if t < datetime.now(UTC):
            t = datetime.now(UTC) + timedelta(minutes=5)
        params: dict[str, Any] = {
            "fromPlace": await self.station_for(o.place),
            "toPlace": await self.station_for(d.place),
            "time": t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arriveBy": str(arrive_by).lower(),
            "numItineraries": 6,
            "transitModes": _modes(intent),
        }
        if (req := intent.requirement("max_transfers")) is not None:
            params["maxTransfers"] = int(req.value)
        if intent.has_requirement("direct"):
            params["maxTransfers"] = 0
        data = await self.http.get_json(f"{get_settings().transitous_url}/api/v5/plan", params=params)
        out: list[Result] = []
        for it in (data or {}).get("itineraries", []):
            r = itinerary_to_result(it, intent.domain)
            if r is not None:
                out.append(r)
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
