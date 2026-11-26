"""Regression dataset for the rule parser. Every case must pass; add a case for every parser bug fixed."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from radar.nlu.rules import parse

CASES = json.loads((Path(__file__).parent / "parser_cases.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 26)
# Thursday 17:42 local time of the place being searched (wall clock, used by 'now', 'in 2 hours').
NOW = datetime(2026, 11, 26, 17, 42, tzinfo=UTC)


def _name(ref):
    return ref.place.name if ref and ref.place else None


@pytest.mark.parametrize("case", CASES, ids=[c["text"][:40] for c in CASES])
def test_case(case):
    i = parse(case["text"], today=TODAY, now=NOW).intent
    assert i.domain.value == case["domain"]
    if "location" in case:
        assert _name(i.location) == case["location"]
    if "origin" in case:
        assert _name(i.origin) == case["origin"]
    if "destination" in case:
        assert _name(i.destination) == case["destination"]
    if "adults" in case:
        assert i.party.adults == case["adults"]
    if "date" in case:
        got = i.when.start.date().isoformat() if i.when and i.when.start else None
        assert got == case["date"]
    if "time" in case:
        assert i.when.start.strftime("%H:%M") == case["time"]
    if "check_in" in case:
        assert (i.stay.check_in.isoformat(), i.stay.check_out.isoformat()) == (case["check_in"], case["check_out"])
    for cat in case.get("categories", []):
        assert cat in i.categories
    req = {c.key for c in i.requirements}
    for key in case.get("requirements", []):
        assert key in req
    if "budget" in case:
        amount, rest = case["budget"].split(" ")
        cur, _, unit = rest.partition("/")
        assert i.budget.max.amount == Decimal(amount) and i.budget.max.currency == cur
        if unit:
            assert i.budget.unit.value == unit
    if "plan_kind" in case:
        assert i.plan_kind.value == case["plan_kind"]
    if "monitor" in case:
        assert i.monitor and i.monitor.kind.value == case["monitor"]
