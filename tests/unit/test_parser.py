"""The request examples from the product brief, end to end through the rule parser."""

from datetime import date, time
from decimal import Decimal

from radar.domain.common import BudgetUnit, Domain, SortMode
from radar.domain.intents import MonitorKind, PlanKind
from radar.nlu.rules import detect_language, parse

T = date(2026, 11, 26)


def p(text):
    return parse(text, today=T).intent


def keys(cs):
    return {c.key for c in cs}


def test_sushi_yerevan():
    i = p("Find a sushi restaurant for two in Yerevan tomorrow at 18:00. Romantic atmosphere, maximum 30,000 AMD.")
    assert i.domain is Domain.DINING
    assert "cuisine:sushi" in i.categories
    assert i.location.place.canonical_id == "gz:am-yerevan"
    assert i.party.adults == 2
    assert i.when.start.time() == time(18, 0) and i.when.start.date() == date(2026, 11, 27)
    assert str(i.when.start.tzinfo) == "Asia/Yerevan"
    assert i.budget.max.amount == Decimal("30000") and i.budget.max.currency == "AMD"
    # Atmosphere cannot be verified, so it is never a hard requirement.
    assert "atmosphere.romantic" in keys(i.preferences)
    assert not i.missing


def test_berlin_apartment_is_short_stay():
    i = p(
        "Find an apartment in Berlin for two adults from December 21 to December 27. Queen-size bed, free Wi-Fi, "
        "private bathroom, central location, maximum EUR 600 total."
    )
    assert i.domain is Domain.STAY
    assert (i.stay.check_in, i.stay.check_out, i.stay.nights) == (date(2026, 12, 21), date(2026, 12, 27), 6)
    assert {"bed.queen", "wifi", "private_bathroom"} <= keys(i.requirements)
    assert i.budget.unit is BudgetUnit.TOTAL and i.budget.max.amount == 600


def test_train_arrive_by_prefers_direct():
    i = p("Find a train from Frankfurt to Berlin arriving before 14:00 tomorrow. Prefer direct trains.")
    assert i.domain is Domain.RAIL
    assert i.origin.place.name == "Frankfurt" and i.destination.place.name == "Berlin"
    assert i.arrive_by and i.when.end.time() == time(14, 0)
    assert "direct" in keys(i.preferences) and "direct" not in keys(i.requirements)


def test_multimodal_cheapest():
    i = p(
        "Find the cheapest way to travel from Yerevan to Sicily next month. Include combinations of flights, "
        "trains, ferries and buses."
    )
    assert i.domain is Domain.GROUND
    assert "mode:multimodal" in i.categories
    assert i.sort is SortMode.CHEAPEST
    assert i.destination.place.canonical_id == "gz:it-sicily"
    assert i.when.start.date() == date(2026, 12, 1)


def test_spa_sensitive_requirement():
    i = p("Find a spa for two people near Wiesbaden on Saturday with female massage therapists available.")
    assert i.domain is Domain.WELLNESS
    assert i.location.radius_m == 20_000
    assert "staff.female" in keys(i.requirements)
    assert i.when.start.date() == date(2026, 11, 28)


def test_long_term_rental():
    i = p(
        "Find a long-term apartment in Tbilisi with two bedrooms, a balcony, pet-friendly policy and a maximum "
        "monthly rent of USD 800."
    )
    assert i.domain is Domain.REAL_ESTATE
    assert i.budget.unit is BudgetUnit.MONTH and i.budget.max.currency == "USD"
    assert {"balcony", "pet_friendly", "bedrooms.min", "deal.rent"} <= keys(i.requirements)


def test_price_alert():
    i = p("Notify me whenever a direct flight from Yerevan to Rome drops below EUR 100.")
    assert i.domain is Domain.FLIGHT
    assert i.monitor.kind is MonitorKind.PRICE_BELOW and i.monitor.threshold.amount == 100
    assert "direct" in keys(i.requirements)


def test_plan_romantic_weekend():
    i = p("Plan a romantic weekend for two in Paris for EUR 700, excluding transportation to Paris.")
    assert i.domain is Domain.PLAN and i.plan_kind is PlanKind.ROMANTIC_WEEKEND
    assert "exclude:transport" in i.categories
    assert i.origin is None and i.location.place.name == "Paris"


def test_hotel_late_checkin_without_city_asks():
    i = p("Find a hotel near the train station with late check-in because my train arrives at midnight.")
    assert i.domain is Domain.STAY
    assert "late_checkin" in keys(i.requirements)
    assert "near:station" in i.categories
    assert i.missing[:2] == ["location", "dates"]


def test_russian_and_armenian():
    i = p("Найди грузинский ресторан в Тбилиси на двоих завтра в 19:00 до 100 лари")
    assert (i.domain, i.language, i.party.adults) == (Domain.DINING, "ru", 2)
    assert i.budget.max.currency == "GEL" and "cuisine:georgian" in i.categories
    i = p("Երևանից Թբիլիսի ավտոբուս վաղը")
    assert i.domain is Domain.GROUND and i.language == "hy"
    assert i.origin.place.name == "Yerevan" and i.destination.place.name == "Tbilisi"


def test_preference_clause():
    i = p("hotel in Rome with breakfast, preferably a balcony, max 120 EUR per night, 3 nights from 21.12")
    assert "breakfast" in keys(i.requirements) and "balcony" in keys(i.preferences)
    assert i.budget.unit is BudgetUnit.NIGHT


def test_unknown_domain_is_reported():
    assert "domain" in p("something nice").missing


def test_language_detection():
    assert detect_language("Zug von München nach Wien morgen") == "de"
    assert detect_language("ხვალ თბილისში") == "ka"
    assert detect_language("ертең Алматыда мейрамхана") == "kk"
