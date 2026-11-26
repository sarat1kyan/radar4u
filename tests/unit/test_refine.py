from decimal import Decimal

from radar.domain.common import SortMode
from radar.geo.resolver import GAZ, GeoResolver
from radar.nlu.interpreter import Interpreter, UserContext
from tests.conftest import TODAY


async def run(it, text, ctx, prev=None):
    return await it.interpret(text, ctx, previous=prev, today=TODAY, online=False)


async def test_refinements_preserve_constraints():
    it, ctx = Interpreter(GeoResolver()), UserContext()
    r = await run(it, "hotel in Berlin Dec 21 to Dec 27 for two, queen-size bed, wifi, max EUR 600 total", ctx)
    r = await run(it, "make it cheaper", ctx, r.intent)
    assert r.refined and r.intent.budget.max.amount == Decimal("480.00") and r.intent.sort is SortMode.CHEAPEST
    r = await run(it, "actually Paris", ctx, r.intent)
    assert r.intent.location.place.name == "Paris"
    assert {c.key for c in r.intent.requirements} == {"bed.queen", "wifi"}
    assert r.intent.stay.nights == 6
    r = await run(it, "add a balcony", ctx, r.intent)
    assert r.intent.has_requirement("balcony")
    r = await run(it, "without wifi", ctx, r.intent)
    assert not r.intent.has_requirement("wifi") and r.intent.has_requirement("bed.queen")


async def test_time_only_followup_keeps_date():
    it, ctx = Interpreter(GeoResolver()), UserContext()
    r = await run(it, "sushi in Yerevan tomorrow", ctx)
    r = await run(it, "at 20:00 instead", ctx, r.intent)
    assert r.intent.when.start.isoformat() == "2026-11-27T20:00:00+04:00"


async def test_context_fills_origin_not_asks():
    it = Interpreter(GeoResolver())
    ctx = UserContext(home=GAZ.lookup("Ереван").to_place())
    r = await run(it, "flight to Rome next month", ctx)
    assert r.used_context_location and r.intent.origin.place.name == "Yerevan" and not r.intent.missing


async def test_new_search_is_not_a_refinement():
    it, ctx = Interpreter(GeoResolver()), UserContext()
    r = await run(it, "sushi in Yerevan tomorrow", ctx)
    r2 = await run(it, "Find a train from Frankfurt to Berlin tomorrow", ctx, r.intent)
    assert not r2.refined and r2.intent.domain.value == "rail"
