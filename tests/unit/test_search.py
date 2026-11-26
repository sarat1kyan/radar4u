import asyncio
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from radar.config import Settings
from radar.core.errors import ProviderUnavailable
from radar.core.money import Money
from radar.core.provenance import Freshness, Provenance, Tri
from radar.domain.common import BudgetUnit, Domain, GeoPoint, SortMode
from radar.domain.results import Price, Rating, Result
from radar.nlu.rules import parse
from radar.providers.base import AccessLevel, Capability, Provider, SearchContext
from radar.providers.fixtures import FixtureProvider
from radar.providers.registry import ProviderRegistry
from radar.search.dedup import dedupe
from radar.search.filters import Tier, evaluate, price_for_budget
from radar.search.pipeline import SearchPipeline
from radar.search.ranking import Ranked, rank, rating_strength

T = date(2026, 11, 26)


def intent(text):
    return parse(text, today=T).intent


def place_result(title, attrs=None, hours=None, price=None, rating=None, dist=100, provider="osm"):
    return Result(
        domain=Domain.DINING,
        title=title,
        attributes=attrs or {},
        facts={"opening_hours": hours},
        price=price,
        rating=rating,
        distance_m=dist,
        point=GeoPoint(lat=40.18, lon=44.51 + dist / 1e6),
        provenance=Provenance(provider=provider, freshness=Freshness.DIRECTORY, external_id=title),
    )


def test_unknown_amenity_is_never_exact():
    i = intent("restaurant in Yerevan with outdoor seating tomorrow at 19:00")
    yes = evaluate(place_result("A", {"outdoor_seating": Tri.YES}, "Mo-Su 12:00-23:00"), i)
    unk = evaluate(place_result("B", {}, "Mo-Su 12:00-23:00"), i)
    no = evaluate(place_result("C", {"outdoor_seating": Tri.NO}, "Mo-Su 12:00-23:00"), i)
    closed = evaluate(place_result("D", {"outdoor_seating": Tri.YES}, "Mo-Su 08:00-16:00"), i)
    assert (yes.tier, unk.tier, no.tier, closed.tier) == (
        Tier.EXACT,
        Tier.UNVERIFIED,
        Tier.ALTERNATIVE,
        Tier.ALTERNATIVE,
    )
    assert "open_at" in closed.unmet and "outdoor_seating" in unk.unverified


def test_budget_units_and_unknown_price():
    i = intent("hotel in Rome 3 nights from 21.12, max 120 EUR per night")
    r = place_result("H", price=Price(amount=Money.of("330", "EUR"), unit=BudgetUnit.TOTAL))
    r.domain = Domain.STAY
    assert price_for_budget(r, i, None).amount == Decimal("110")
    assert "budget" in evaluate(r, i).met
    r.price = Price(amount=Money.of("330", "EUR"), payable_at_property=Money.of("60", "EUR"))
    assert "budget" in evaluate(r, i).unmet  # 330/3 + 60 city tax > 120
    r.price = None
    assert "budget" in evaluate(r, i).unverified


def test_preferences_do_not_change_tier():
    i = intent("restaurant in Yerevan, preferably outdoor seating")
    rep = evaluate(place_result("A", {"outdoor_seating": Tri.NO}), i)
    assert rep.tier is Tier.EXACT and rep.pref_unmet == ["outdoor_seating"]


def test_rating_shrinkage_prefers_more_reviews():
    few = place_result("few", rating=Rating(value=5.0, scale=5, count=2, source="x"))
    many = place_result("many", rating=Rating(value=4.6, scale=5, count=900, source="x"))
    assert rating_strength(many) > rating_strength(few)


def test_tier_dominates_score():
    i = intent("restaurant in Yerevan with outdoor seating")
    cheap_alt = place_result("Alt", {"outdoor_seating": Tri.NO}, price=Price(amount=Money.of(1, "AMD")), dist=1)
    exact = place_result("Exact", {"outdoor_seating": Tri.YES}, dist=5000)
    items = [Ranked(result=r, report=evaluate(r, i)) for r in (cheap_alt, exact)]
    ranked = rank(items, i, SortMode.CHEAPEST)
    assert [x.result.title for x in ranked] == ["Exact", "Alt"]


def test_transport_window_arrive_by():
    i = intent("train from Frankfurt to Berlin arriving before 14:00 tomorrow")
    z = ZoneInfo("Europe/Berlin")

    def j(dep, arr):
        return Result(
            domain=Domain.RAIL,
            title=f"{dep}",
            starts_at=datetime(2026, 11, 27, dep, tzinfo=z),
            ends_at=datetime(2026, 11, 27, arr, tzinfo=z),
            provenance=Provenance(provider="t", freshness=Freshness.TIMETABLE, external_id=str(dep)),
        )

    assert "time_window" in evaluate(j(9, 13), i).met
    assert "time_window" in evaluate(j(10, 15), i).unmet


def test_dedupe_merges_same_venue_across_sources():
    a = place_result("Café Central", {"wifi": Tri.UNKNOWN}, dist=100)
    b = place_result("Cafe Central", {"wifi": Tri.YES}, dist=120, provider="other")
    b.provenance = b.provenance.model_copy(update={"freshness": Freshness.LIVE})
    out = dedupe([a, b])
    assert len(out) == 1 and out[0].provenance.provider == "other" and out[0].facts["also_from"] == ["osm"]


class Boom(Provider):
    name = "boom"
    capabilities = (Capability(Domain.STAY),)

    async def search(self, intent, ctx):
        raise ProviderUnavailable("boom", "down")


class Slow(Provider):
    name = "slow"
    capabilities = (Capability(Domain.STAY),)

    async def search(self, intent, ctx):
        await asyncio.sleep(30)
        return []


def settings(**kw):
    return Settings(RADAR_ENV="test", RADAR_ENABLE_FIXTURES=True, **kw)


async def test_pipeline_partial_results_and_timeout():
    reg = ProviderRegistry([FixtureProvider(), Boom(), Slow()], settings())
    reg.providers[0].access = AccessLevel.SELF_SERVE_KEY  # pretend it is live so fixtures are not filtered
    pipe = SearchPipeline(reg, timeout_s=1)
    i = intent("hotel in Berlin from December 21 to December 27 for two, wifi, max EUR 2000 total")
    seen = []

    async def progress(name, done, total):
        seen.append((name, done, total))

    out = await pipe.run(i, SearchContext(currency="EUR"), on_progress=progress)
    assert out.provider_errors == {"boom": "provider_unavailable", "slow": "timeout"}
    assert out.timed_out and len(out.ranked) == 5
    assert {s[0] for s in seen} >= {"fixtures", "boom"}
    # queen bed was not requested; wifi YES on all demo stays -> exact matches exist
    assert out.exact


async def test_registry_explains_skips():
    s = settings(RADAR_COMMERCIAL=True)
    from radar.providers.transitous import TransitousProvider

    reg = ProviderRegistry([TransitousProvider()], s)
    el = reg.eligible(intent("train from Frankfurt to Berlin tomorrow"))
    assert el.empty and el.skipped == {"transitous": "non_commercial_terms"}


async def test_fixtures_never_padded_next_to_live():
    class Live(Provider):
        name = "live"
        access = AccessLevel.SELF_SERVE_KEY
        capabilities = (Capability(Domain.STAY),)

        async def search(self, intent, ctx):
            return []

    reg = ProviderRegistry([FixtureProvider(), Live()], settings())
    assert [p.name for p in reg.eligible(intent("hotel in Berlin Dec 21 to Dec 27")).providers] == ["live"]


async def test_no_coverage_outcome():
    reg = ProviderRegistry([], settings())
    out = await SearchPipeline(reg).run(intent("flight from Yerevan to Rome on December 5"), SearchContext())
    assert out.no_coverage and not out.ranked
