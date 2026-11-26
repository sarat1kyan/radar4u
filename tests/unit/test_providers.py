import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from radar.core.errors import NotConfigured
from radar.core.provenance import Freshness, Tri
from radar.domain.common import Domain
from radar.domain.results import Availability, BookingMode, PriceKind
from radar.nlu.rules import parse
from radar.providers.base import SearchContext
from radar.providers.duffel import DuffelProvider, iso_duration_min
from radar.providers.feeds import ListingFeedProvider, load_feed_configs
from radar.providers.fx import FxService, _parse_ecb
from radar.providers.http import ProviderHttp
from radar.providers.liteapi import LiteApiProvider
from radar.providers.osm_places import OsmPlacesProvider, build_query
from radar.providers.ticketmaster import TicketmasterProvider, geohash
from radar.providers.transitous import TransitousProvider, itinerary_to_result
from radar.providers.travelpayouts import TravelpayoutsProvider

FIX = Path(__file__).parent.parent / "fixtures"
T = date(2026, 11, 26)


def intent(text):
    return parse(text, today=T).intent


def fast_http(name):
    return ProviderHttp(name, rate_per_s=1000, burst=100, max_retries=0)


def test_overpass_query_has_cuisine_filter_and_radius():
    i = intent("sushi restaurant in Yerevan")
    q = build_query(i, 40.18, 44.51, 5000, 40)
    assert '["cuisine"~"sushi",i]' in q and "[bbox:40.13508,44.45121,40.22492,44.56879]" in q
    assert q.startswith("[out:json]")


@respx.mock
async def test_osm_results_are_directory_and_tri_state():
    respx.post(url__regex=r".*interpreter").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "overpass_dining.json").read_text()))
    )
    p = OsmPlacesProvider(http=fast_http("osm"))
    rs = await p.search(intent("sushi in Yerevan"), SearchContext(locale="ru"))
    assert [r.title for r in rs] == ["Васаби", "Late Sushi", "Unknown Hours Sushi"]  # nameless element dropped
    w = rs[0]
    assert w.provenance.freshness is Freshness.DIRECTORY and w.availability is Availability.UNKNOWN
    assert w.attr("outdoor_seating") is Tri.YES and w.attr("wifi") is Tri.UNKNOWN
    assert rs[1].attr("outdoor_seating") is Tri.NO
    assert w.booking is BookingMode.HANDOFF and w.price is None and w.rating is None
    assert w.facts["description"].startswith("Ignore previous")  # kept as inert display text only


def test_transitous_itinerary_mapping():
    it = {
        "transfers": 1,
        "legs": [
            {"mode": "WALK", "from": {}, "to": {}},
            {
                "mode": "HIGHSPEED_RAIL",
                "agencyName": "DB Fernverkehr AG",
                "displayName": "ICE 1557",
                "duration": 7200,
                "from": {
                    "name": "Frankfurt Hbf",
                    "tz": "Europe/Berlin",
                    "departure": "2026-11-27T08:19:00Z",
                    "lat": 50.1,
                    "lon": 8.6,
                    "track": "7",
                },
                "to": {
                    "name": "Erfurt Hbf",
                    "tz": "Europe/Berlin",
                    "arrival": "2026-11-27T10:30:00Z",
                    "lat": 50.9,
                    "lon": 11.0,
                },
            },
            {
                "mode": "HIGHSPEED_RAIL",
                "agencyName": "DB Fernverkehr AG",
                "displayName": "ICE 1100",
                "duration": 7000,
                "from": {"name": "Erfurt Hbf", "tz": "Europe/Berlin", "departure": "2026-11-27T10:41:00Z"},
                "to": {"name": "Berlin Hbf", "tz": "Europe/Berlin", "arrival": "2026-11-27T12:40:00Z"},
            },
        ],
    }
    r = itinerary_to_result(it, Domain.RAIL)
    assert r.domain is Domain.RAIL and r.transfers == 1
    assert r.connections[0].minutes == 11 and not r.connections[0].station_change
    assert r.legs[0].origin.platform == "7"
    assert str(r.starts_at.tzinfo) == "Europe/Berlin" and r.starts_at.hour == 9
    assert r.provenance.freshness is Freshness.TIMETABLE and r.price is None
    assert r.links[0].url == "https://www.bahn.de"
    assert r.attr("direct") is Tri.NO and r.attr("bicycle") is Tri.UNKNOWN


@respx.mock
async def test_transitous_search_uses_station_ids():
    respx.get(url__regex=r".*/api/v1/geocode.*").mock(
        return_value=httpx.Response(
            200, json=[{"type": "STOP", "id": "de:frankfurt-hbf", "lat": 50.1069, "lon": 8.6627, "importance": 0.9}]
        )
    )
    plan = respx.get(url__regex=r".*/api/v5/plan.*").mock(return_value=httpx.Response(200, json={"itineraries": []}))
    p = TransitousProvider(http=fast_http("transitous"))
    await p.search(intent("train from Frankfurt to Berlin tomorrow, direct"), SearchContext())
    qs = dict(plan.calls[0].request.url.params)
    assert qs["fromPlace"] == "de:frankfurt-hbf" and qs["transitModes"] == "RAIL" and qs["maxTransfers"] == "0"


@respx.mock
async def test_duffel_offers_mapping():
    route = respx.post("https://api.duffel.com/air/offer_requests").mock(
        return_value=httpx.Response(200, json=json.loads((FIX / "duffel_offer_request.json").read_text()))
    )
    p = DuffelProvider(token="duffel_test_x", http=fast_http("duffel"))
    rs = await p.search(intent("flight from Yerevan to Rome on December 5"), SearchContext())
    req = route.calls[0].request
    assert req.headers["Duffel-Version"] == "v2" and req.headers["Authorization"] == "Bearer duffel_test_x"
    body = json.loads(req.content)
    assert body["data"]["slices"][0] == {"origin": "EVN", "destination": "ROM", "departure_date": "2026-12-05"}
    direct, onestop = rs
    assert direct.price.amount.amount == Decimal("182.40") and direct.price.kind is PriceKind.OFFER
    assert direct.attr("direct") is Tri.YES and direct.attr("checked_bag") is Tri.YES
    assert direct.attr("refundable") is Tri.NO and direct.attr("changeable") is Tri.YES
    assert str(direct.starts_at.tzinfo) == "Asia/Yerevan"
    assert onestop.connections[0].self_transfer is Tri.NO and onestop.connections[0].minutes == 80
    assert direct.provenance.freshness is Freshness.FIXTURE  # test mode is labeled as such


async def test_unconfigured_provider_refuses():
    with pytest.raises(NotConfigured):
        await DuffelProvider(token=None).search(intent("flight from Yerevan to Rome on December 5"), SearchContext())


def test_iso_duration():
    assert iso_duration_min("PT4H30M") == 270 and iso_duration_min("P1DT2H") == 1560


@respx.mock
async def test_travelpayouts_is_estimate():
    respx.get(url__regex=r".*prices_for_dates.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "currency": "eur",
                "data": [
                    {
                        "origin_airport": "EVN",
                        "destination_airport": "FCO",
                        "price": 96,
                        "airline": "W6",
                        "flight_number": "4410",
                        "departure_at": "2026-12-05T06:00:00+04:00",
                        "transfers": 0,
                        "link": "/search/EVN0512ROM1",
                    }
                ],
            },
        )
    )
    p = TravelpayoutsProvider(token="t", http=fast_http("tp"))
    rs = await p.search(intent("flight from Yerevan to Rome next month"), SearchContext())
    r = rs[0]
    assert r.price.kind is PriceKind.ESTIMATE and r.provenance.freshness is Freshness.CACHED_ESTIMATE
    assert r.availability is Availability.UNKNOWN and r.links[0].url.startswith("https://www.aviasales.com/search")


@respx.mock
async def test_liteapi_rates_and_taxes():
    respx.post("https://api.liteapi.travel/v3.0/hotels/rates").mock(
        return_value=httpx.Response(
            200,
            json={
                "sandbox": True,
                "hotels": [{"id": "lp1", "name": "Hotel Uno", "latitude": 52.52, "longitude": 13.40, "rating": 8.6}],
                "data": [
                    {
                        "hotelId": "lp1",
                        "roomTypes": [
                            {
                                "offerId": "OFR1",
                                "offerRetailRate": {"amount": 540, "currency": "EUR"},
                                "rates": [
                                    {
                                        "name": "Double Room",
                                        "boardType": "BB",
                                        "cancellationPolicies": {"refundableTag": "RFN"},
                                        "retailRate": {
                                            "taxesAndFees": [{"included": False, "amount": 18, "currency": "EUR"}]
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
    )
    p = LiteApiProvider(key="sand_x", http=fast_http("lite"))
    rs = await p.search(intent("hotel in Berlin from December 21 to December 27"), SearchContext())
    r = rs[0]
    assert r.price.amount.amount == 540 and r.price.payable_at_property.amount == 18
    assert r.attr("breakfast") is Tri.YES and r.attr("free_cancellation") is Tri.YES
    assert r.attr("bed.queen") is Tri.UNKNOWN  # room-level amenity not in rates payload
    assert r.facts["offer_id"] == "OFR1" and r.provenance.freshness is Freshness.FIXTURE


def test_geohash_known_value():
    assert geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"


@respx.mock
async def test_ticketmaster_events():
    respx.get(url__regex=r".*/events\.json.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "_embedded": {
                    "events": [
                        {
                            "id": "E1",
                            "name": "Concert X",
                            "url": "https://www.ticketmaster.de/event/E1",
                            "dates": {
                                "start": {"dateTime": "2026-11-28T19:00:00Z", "localDate": "2026-11-28"},
                                "status": {"code": "onsale"},
                            },
                            "priceRanges": [{"min": 39.9, "max": 120, "currency": "EUR"}],
                            "_embedded": {
                                "venues": [{"name": "Arena", "location": {"latitude": "52.5", "longitude": "13.4"}}]
                            },
                        }
                    ]
                }
            },
        )
    )
    p = TicketmasterProvider(key="k", http=fast_http("tm"))
    rs = await p.search(intent("concert in Berlin on Saturday"), SearchContext())
    assert rs[0].price.kind is PriceKind.FROM and rs[0].booking is BookingMode.HANDOFF


@respx.mock
async def test_listing_feed_filters_radius_and_numbers():
    cfg = load_feed_configs(
        '[{"name": "partner", "url": "https://feed.example/l.json", "domain": "realestate", '
        '"countries": ["GE"], "attribution": "Partner feed"}]'
    )[0]
    respx.get("https://feed.example/l.json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "a",
                    "title": "2BR Vake",
                    "url": "https://partner.example/a",
                    "lat": 41.71,
                    "lon": 44.76,
                    "bedrooms": 2,
                    "deal": "rent",
                    "balcony": True,
                    "price": {"amount": "750", "currency": "USD", "unit": "month"},
                },
                {"id": "b", "title": "Far away", "url": "https://partner.example/b", "lat": 42.5, "lon": 41.9},
                {"id": "c", "title": "No https", "url": "http://partner.example/c", "lat": 41.71, "lon": 44.76},
            ],
        )
    )
    p = ListingFeedProvider(cfg, http=fast_http("feed"))
    rs = await p.search(intent("long-term apartment in Tbilisi with two bedrooms"), SearchContext())
    assert [r.title for r in rs] == ["2BR Vake"]
    assert rs[0].attr("bedrooms.min") is Tri.YES and rs[0].attr("pet_friendly") is Tri.UNKNOWN


def test_ecb_parser():
    xml = """<?xml version="1.0"?><gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-11-25">
    <Cube currency="USD" rate="1.05"/><Cube currency="GBP" rate="0.83"/></Cube></Cube></gesmes:Envelope>"""
    rates, as_of = _parse_ecb(xml)
    assert rates["USD"] == Decimal("1.05") and as_of.date() == date(2026, 11, 25)


async def test_fx_cross_rate_from_seed():
    from datetime import UTC, datetime

    fx = FxService()
    fx.seed("ecb", {"EUR": Decimal(1), "USD": Decimal("1.10")}, datetime(2026, 11, 25, tzinfo=UTC))
    fx.seed("open_er", {"EUR": Decimal(1), "AMD": Decimal("420")}, datetime(2026, 11, 25, tzinfo=UTC))
    r = await fx.rate("USD", "EUR")
    assert r.source == "ECB reference rate" and r.rate.quantize(Decimal("0.0001")) == Decimal("0.9091")
    r = await fx.rate("EUR", "AMD")
    assert "Exchange Rate API" in r.source
