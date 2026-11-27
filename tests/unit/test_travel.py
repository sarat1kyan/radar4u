from datetime import UTC, datetime
from decimal import Decimal

import pytest

from radar import travel
from radar.domain.common import Domain, GeoPoint, Place, PlaceKind
from radar.domain.intents import SearchIntent
from radar.nlu.fx_query import parse_conversion
from radar.providers.osm_places import build_query, element_to_result
from radar.providers.weather import CurrentWeather

YEREVAN = Place(
    canonical_id="geo:40.18,44.51",
    name="Kentron, Yerevan",
    kind=PlaceKind.COORDINATES,
    country_code="AM",
    point=GeoPoint(lat=40.1811, lon=44.5136),
    timezone="Asia/Yerevan",
)


@pytest.mark.parametrize(
    ("text", "amount", "base", "quote"),
    [
        ("100 usd", "100", "USD", None),
        ("50 eur to amd", "50", "EUR", "AMD"),
        ("$20 в драмах", "20", "USD", "AMD"),
        ("1 000 драм", "1000", "AMD", None),
        ("convert 250 gel to usd", "250", "GEL", "USD"),
        ("20€", "20", "EUR", None),
        ("15 долларов", "15", "USD", None),
    ],
)
def test_conversion_parsed(text, amount, base, quote):
    c = parse_conversion(text)
    assert c is not None and c.amount == Decimal(amount) and c.base == base and c.quote == quote


@pytest.mark.parametrize("text", ["hotel under 100 eur", "100 eur hotel", "100", "table for 2 people", "at 10 pm"])
def test_not_a_conversion(text):
    assert parse_conversion(text) is None


def test_country_tables():
    assert travel.country_currency("AM") == "AMD"
    assert travel.country_currency("DE") == "EUR"
    assert travel.country_currency("XX") is None
    assert travel.emergency_primary("AM") == "911"
    assert travel.emergency_primary("JP") == "110"
    assert travel.emergency_primary("ZZ") == "112"


class FakeWeather:
    async def current(self, point):
        return CurrentWeather(condition="clear", temperature=4.2, feels_like=0.5, wind_kmh=10, is_day=True)


async def test_snapshot(fx_seeded):
    snap = await travel.snapshot(
        YEREVAN, home_tz="Europe/Berlin", user_currency="EUR", weather=FakeWeather(), fx=fx_seeded
    )
    assert snap.currency == "AMD" and snap.rate is not None and snap.rate.rate > 100
    assert snap.weather is not None and snap.weather.condition == "clear"
    assert snap.offset_h == 3.0  # Yerevan UTC+4 vs Berlin UTC+1 in November
    assert snap.emergency == {"all": "911", "alt": "112"}


def test_unnamed_atm_kept_with_brand():
    intent = SearchIntent(domain=Domain.ESSENTIALS, categories=["place:atm"])
    q = build_query(intent, 40.18, 44.51, 1000, 20)
    assert '["amenity"="atm"];' in q  # no name filter for ATMs
    el = {"type": "node", "id": 1, "lat": 40.18, "lon": 44.51, "tags": {"amenity": "atm", "operator": "Ameriabank"}}
    r = element_to_result(el, intent, "en", GeoPoint(lat=40.18, lon=44.51))
    assert r is not None and r.title == "Ameriabank"
    el["tags"] = {"amenity": "atm"}
    assert element_to_result(el, intent, "en", GeoPoint(lat=40.18, lon=44.51)).title == "ATM"
    dining = SearchIntent(domain=Domain.DINING)
    assert element_to_result({**el, "tags": {"amenity": "cafe"}}, dining, "en", GeoPoint(lat=40.18, lon=44.51)) is None


@pytest.fixture
def fx_seeded():
    from radar.providers.fx import FxService

    fx = FxService()
    now = datetime.now(UTC)
    fx.seed("ecb", {"EUR": Decimal(1), "USD": Decimal("1.08")}, now)
    fx.seed("open_er", {"EUR": Decimal(1), "USD": Decimal("1.08"), "AMD": Decimal("420.5")}, now)
    return fx
