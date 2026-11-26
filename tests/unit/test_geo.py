from radar.domain.common import GeoPoint, PlaceKind
from radar.geo.resolver import GAZ, GeoResolver, timezone_for


async def test_gazetteer_scripts_and_aliases():
    r = GeoResolver()
    for name in ("Yerevan", "Ереван", "Երևան", "Erevan"):
        ref = await r.resolve(name, online=False)
        assert ref.place and ref.place.canonical_id == "gz:am-yerevan", name
    assert (await r.resolve("Tiflis", online=False)).place.name == "Tbilisi"
    assert (await r.resolve("München", online=False)).place.timezone == "Europe/Berlin"


async def test_iata_and_coordinates():
    r = GeoResolver()
    assert (await r.resolve("EVN", online=False)).place.iata == "EVN"
    ref = await r.resolve("40.18, 44.51", online=False)
    assert ref.place.kind is PlaceKind.COORDINATES and ref.place.country_code == "AM"


def test_inflected_mentions():
    hits = GAZ.find_in_text("ужин в Ереване и потом в Париже")
    assert [e.name for _, _, e in hits] == ["Yerevan", "Paris"]


def test_timezone_for_multizone_country():
    assert timezone_for(GeoPoint(lat=43.25, lon=76.9), "KZ") == "Asia/Almaty"
    assert timezone_for(GeoPoint(lat=40.2, lon=44.5), "AM") == "Asia/Yerevan"
