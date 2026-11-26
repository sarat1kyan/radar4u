"""Conversational refinement of an existing intent.

"make it cheaper", "actually Paris", "for three", "add a balcony", "without
parking": the previous intent is kept and only what the message changes is
changed. Hard requirements are never dropped unless the user says so.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from radar.core.money import Money
from radar.core.timeutil import tz
from radar.domain.common import Budget, DateWindow, Domain, LocationRef, SortMode, StayDates
from radar.domain.intents import SearchIntent
from radar.domain.vocabulary import CONSTRAINTS
from radar.nlu import lexicon as L
from radar.nlu.dates import DateParse, parse_dates
from radar.nlu.rules import FLAGS, ParseOutcome, _required_missing, parse

CHEAPER = (
    r"\bcheaper\b|\bless expensive\b|\blower (the )?(budget|price)\b|\bmore affordable\b|дешевле|подешевле|"
    r"\bbilliger\b|\bgünstiger\b|\bmoins cher\b|\bpiù economic\w*|\bmás barat\w*|ավելի էժան|უფრო იაფ\w*|арзанырақ"
)
PRICIER = r"\bmore expensive\b|\bhigher budget\b|\bincrease (the )?budget\b|подороже|дороже|\bteurer\b|\bplus cher\b"
NEGATION = (
    r"\b(?:without|no need for|don't need|do not need|remove|drop|no)\s+(?P<w1>[\w\- ]{3,30})|"
    r"\b(?:без|не нужн\w*|убери\w*)\s+(?P<w2>[\w\- ]{3,30})|\bohne\s+(?P<w3>[\w\- ]{3,30})|"
    r"\bsans\s+(?P<w4>[\w\- ]{3,30})|\bsenza\s+(?P<w5>[\w\- ]{3,30})|\bsin\s+(?P<w6>[\w\- ]{3,30})"
)
NEW_SEARCH = r"\bnew search\b|\bstart over\b|\bforget (it|that)\b|новый поиск|\bneue suche\b|\bnouvelle recherche\b"
FOLLOWUP_HINT = (
    r"^\s*(actually|instead|and|also|what about|how about|make it|change|only|but|а|и|лучше|давай|тогда|"
    r"eigentlich|lieber|plutôt|et|invece|mejor|en cambio)\b"
)


def is_followup(text: str, previous: SearchIntent | None, fresh: ParseOutcome) -> bool:
    if previous is None or re.search(NEW_SEARCH, text, FLAGS):
        return False
    if re.search(FOLLOWUP_HINT, text, FLAGS) or re.search(CHEAPER, text, FLAGS) or re.search(PRICIER, text, FLAGS):
        return True
    words = len(text.split())
    # A short message without a confident new domain refines the current search.
    if fresh.confidence < 0.75 or "domain" in fresh.intent.missing:
        return words <= 12
    return fresh.intent.domain is previous.domain and words <= 6


def _scale(m: Money, factor: str) -> Money:
    return Money(amount=(m.amount * Decimal(factor)), currency=m.currency).quantized()


def refine(previous: SearchIntent, text: str, *, today: date, zone: str | None) -> SearchIntent:
    fresh = parse(text, today=today, zone=zone, ui_language=previous.language, force_domain=previous.domain).intent
    out = previous.model_copy(deep=True)
    out.raw_text = f"{previous.raw_text}\n{text}".strip()

    if re.search(CHEAPER, text, FLAGS):
        if out.budget.max is not None and fresh.budget.max is None:
            out.budget = Budget(max=_scale(out.budget.max, "0.8"), unit=out.budget.unit)
        out.sort = SortMode.CHEAPEST
    elif re.search(PRICIER, text, FLAGS) and out.budget.max is not None:
        out.budget = Budget(max=_scale(out.budget.max, "1.25"), unit=out.budget.unit)

    # Locations: a single new place replaces the primary location (or destination for transport).
    if previous.domain.is_transport:
        if fresh.origin and fresh.destination:
            out.origin, out.destination = fresh.origin, fresh.destination
        else:
            new = fresh.destination or fresh.origin or fresh.location
            if new is not None:
                if re.search(r"\bfrom\b|\bиз\b|\bvon\b|\bdepuis\b|\bda\b|\bdesde\b", text, FLAGS):
                    out.origin = new
                else:
                    out.destination = new
    else:
        new_loc = fresh.location or fresh.destination
        if new_loc is not None:
            out.location = LocationRef(text=new_loc.text, place=new_loc.place, radius_m=new_loc.radius_m)

    dp = parse_dates(text, today)
    if previous.stay is not None or out.domain is Domain.STAY:
        if fresh.stay is not None:
            out.stay = fresh.stay
        elif dp.date_from and previous.stay is not None:
            nights = previous.stay.nights
            out.stay = StayDates(check_in=dp.date_from, check_out=dp.date_from + timedelta(days=nights))
    elif dp.found:
        out.when, out.arrive_by = _merge_when(previous, dp, today, zone)

    if fresh.party != previous.party and _party_mentioned(text):
        out.party = fresh.party
    if fresh.budget.max is not None:
        out.budget = fresh.budget
    if fresh.sort is not SortMode.BEST:
        out.sort = fresh.sort

    for c in fresh.requirements:
        if out.domain in CONSTRAINTS[c.key].domains:
            out.set_requirement(c.key, c.value)
    for c in fresh.preferences:
        if out.domain in CONSTRAINTS[c.key].domains:
            out.set_preference(c.key, c.value)
    _apply_negations(out, text)

    new_cuisine = [c for c in fresh.categories if c.startswith("cuisine:")]
    if new_cuisine:
        out.categories = [c for c in out.categories if not c.startswith("cuisine:")] + new_cuisine
    for c in fresh.categories:
        if not c.startswith("cuisine:") and c not in out.categories and c.split(":")[0] in ("place", "event", "near"):
            out.categories.append(c)
    if fresh.monitor is not None:
        out.monitor = fresh.monitor

    out.missing = _required_missing(out)
    return out


def _merge_when(previous: SearchIntent, dp: DateParse, today: date, zone: str | None) -> tuple[DateWindow, bool]:
    """'at 20:00' after 'tomorrow' keeps tomorrow; 'on Friday' keeps the time of day."""
    prev = previous.when
    prev_start = prev.start if prev else None
    z = prev_start.tzinfo if prev_start is not None and prev_start.tzinfo else tz(zone)
    day = dp.date_from or (prev_start.date() if prev_start else today)
    if dp.arrive_by and dp.time_to:
        return DateWindow(start=datetime.combine(day, time(0, 0), z), end=datetime.combine(day, dp.time_to, z)), True
    if dp.time_from or dp.time_to:
        t1 = dp.time_from or time(0, 0)
        start = datetime.combine(day, t1, z)
        end = datetime.combine(day, dp.time_to, z) if dp.time_to else start
        return DateWindow(start=start, end=max(start, end)), False
    # Only the date changed: keep the previous time of day and window length.
    if prev_start is not None and prev is not None and prev.end is not None:
        length = prev.end - prev_start
        start = datetime.combine(day, prev_start.timetz().replace(tzinfo=None), z)
        if dp.date_to and dp.date_to > day:
            return DateWindow(start=start, end=datetime.combine(dp.date_to, time(23, 59), z)), previous.arrive_by
        return DateWindow(start=start, end=start + length), previous.arrive_by
    start = datetime.combine(day, time(0, 0), z)
    end_day = dp.date_to or day
    return DateWindow(start=start, end=datetime.combine(end_day, time(23, 59), z)), False


def _party_mentioned(text: str) -> bool:
    return bool(
        re.search(
            r"\b(for|для|на|für|pour|per|para)\s+(\d|one|two|three|four|five|six|двоих|троих|четверых|zwei|drei|"
            r"deux|trois|due|tre|dos|tres)\b|\badults?\b|\bpeople\b|\bчеловек\w*|\bpersonen\b|\bpersonnes\b",
            text,
            FLAGS,
        )
    )


def _apply_negations(intent: SearchIntent, text: str) -> None:
    for m in re.finditer(NEGATION, text, FLAGS):
        phrase = next(g for g in m.groups() if g)
        for pat, key, _ in L.CONSTRAINT_CUES:
            if re.search(pat, phrase, FLAGS):
                intent.drop(key)
        for pat, key in L.ATMOSPHERE_CUES:
            if re.search(pat, phrase, FLAGS):
                intent.drop(key)
        if re.search(r"budget|limit|бюджет|limite|límite", phrase, FLAGS):
            intent.budget = Budget(unit=intent.budget.unit)
