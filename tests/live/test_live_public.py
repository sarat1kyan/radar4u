"""Hits real public endpoints. Run with: pytest -m live tests/live"""

from datetime import date, timedelta

import pytest

from radar.geo.resolver import GeoResolver
from radar.nlu.rules import parse
from radar.providers.base import SearchContext
from radar.providers.fx import FxService
from radar.providers.osm_places import OsmPlacesProvider
from radar.providers.transitous import TransitousProvider

pytestmark = pytest.mark.live


async def test_transitous_rail_journey():
    when = (date.today() + timedelta(days=1)).isoformat()
    i = parse(f"train from Frankfurt to Berlin on {when}", today=date.today()).intent
    p = TransitousProvider()
    try:
        rs = await p.search(i, SearchContext())
    finally:
        await p.aclose()
    assert rs and all(r.price is None for r in rs)


async def test_nominatim_resolves_unknown_city():
    r = GeoResolver()
    try:
        ref = await r.resolve("Gyumri, Armenia")
        ref2 = await r.resolve("Kutaisi")
        ref3 = await r.resolve("Shkodër")
    finally:
        await r.aclose()
    assert ref.place and ref2.place and ref3.place and ref3.place.country_code == "AL"


async def test_fx_has_regional_currencies():
    fx = FxService()
    try:
        r = await fx.rate("EUR", "AMD")
    finally:
        await fx.aclose()
    assert r is not None and r.rate > 100


async def test_overpass_venues():
    i = parse("restaurant in Yerevan", today=date.today()).intent
    p = OsmPlacesProvider()
    try:
        rs = await p.search(i, SearchContext())
    finally:
        await p.aclose()
    assert rs
