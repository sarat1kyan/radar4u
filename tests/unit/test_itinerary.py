from datetime import datetime
from zoneinfo import ZoneInfo

from radar.core.money import Money
from radar.domain.common import GeoPoint
from radar.itinerary.planner import Item, ItemKind, Itinerary, replace, travel_minutes, validate

Z = ZoneInfo("Europe/Paris")


def item(title, h1, m1, h2, m2, lat=48.8566, lon=2.3522, **kw):
    return Item(
        kind=ItemKind.ACTIVITY,
        title=title,
        start=datetime(2026, 11, 28, h1, m1, tzinfo=Z),
        end=datetime(2026, 11, 28, h2, m2, tzinfo=Z),
        point=GeoPoint(lat=lat, lon=lon),
        **kw,
    )


def test_overlap_and_tight_connection():
    it = Itinerary(
        title="t",
        items=[item("A", 10, 0, 12, 0), item("B", 11, 30, 13, 0), item("C", 13, 5, 14, 0, lat=48.89, lon=2.40)],
    )
    codes = [w.code for w in validate(it)]
    assert codes == ["overlap", "tight"]


def test_closed_venue_and_weather():
    it = Itinerary(
        title="t",
        weather={"2026-11-28": "rain"},
        items=[
            item("Park", 10, 0, 11, 0, outdoor=True),
            item("Museum", 19, 0, 20, 0, opening_hours="Mo-Su 10:00-18:00"),
        ],
    )
    assert {w.code for w in validate(it)} == {"weather", "closed"}


def test_replace_shifts_dependents_not_locked():
    a = item("Lunch", 12, 0, 13, 0)
    b = item("Museum", 13, 30, 15, 0, depends_on=a.id)
    c = item("Show", 15, 30, 17, 0, depends_on=b.id, locked=True)
    it = Itinerary(title="t", items=[a, b, c])
    replace(it, a.id, item("Long lunch", 12, 0, 14, 0))
    assert it.item(b.id).start.hour == 14 and it.item(b.id).start.minute == 30
    assert it.item(c.id).start.hour == 15  # locked (e.g. a booked ticket) never moves
    assert any(w.code == "overlap" for w in it.warnings)


def test_budget_status_counts_unknowns():
    it = Itinerary(
        title="t",
        budget=Money.of(100, "EUR"),
        items=[
            item("A", 10, 0, 11, 0, price=Money.of(60, "EUR")),
            item("B", 12, 0, 13, 0, price=Money.of(70, "EUR")),
            item("C", 14, 0, 15, 0),
        ],
    )
    total, unknown = it.budget_status()
    assert total.amount == 130 and unknown == 1
    assert any(w.code == "over_budget" for w in validate(it))


def test_travel_estimate_walk_vs_transit():
    a = GeoPoint(lat=48.8566, lon=2.3522)
    assert travel_minutes(a, GeoPoint(lat=48.8600, lon=2.3522)) < 20
    assert travel_minutes(a, GeoPoint(lat=48.90, lon=2.45)) > 20
