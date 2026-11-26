"""Deterministic, offline request parser.

It is the only component that reads free text. It never invents values:
anything it cannot read stays empty and is reported via `missing`, and the
bot offers buttons for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from radar.core.money import Money
from radar.core.timeutil import tz
from radar.domain.common import (
    Budget,
    BudgetUnit,
    DateWindow,
    Domain,
    LocationRef,
    Party,
    SortMode,
    StayDates,
)
from radar.domain.intents import MonitorKind, MonitorSpec, PlanKind, SearchIntent
from radar.domain.vocabulary import CONSTRAINTS
from radar.geo.resolver import GAZ, GazetteerEntry, normalize
from radar.nlu import lexicon as L
from radar.nlu.dates import MONTHS, WEEKDAYS, DateParse, parse_dates

FLAGS = re.IGNORECASE | re.UNICODE

_FROM_WORDS = r"(?:from|out of|из|изо|от|von|ab|aus|de|d'|da|dal|dalla|desde|depuis)"
_TO_WORDS = r"(?:to|into|towards|в|во|на|до|nach|zu|à|au|en|a|in|per|para|hacia|vers)"


@dataclass
class ParseOutcome:
    intent: SearchIntent
    confidence: float
    domain_scores: dict[Domain, float]


def detect_language(text: str, fallback: str = "en") -> str:
    if re.search(r"[԰-֏]", text):
        return "hy"
    if re.search(r"[Ⴀ-ჿ]", text):
        return "ka"
    if re.search(r"[әғқңөұүһі]", text, FLAGS) and re.search(r"[а-я]", text, FLAGS):
        return "kk"
    if re.search(r"[а-яё]", text, FLAGS):
        return "ru"
    low = f" {text.lower()} "
    scores = {
        "de": len(re.findall(r" (und|für|mit|nach|ein|eine|der|die|das|zwischen|morgen|bis) ", low)),
        "fr": len(re.findall(r" (et|pour|avec|une|le|la|les|des|demain|jusqu'à|à) ", low)),
        "it": len(re.findall(r" (e|per|con|una|il|gli|domani|fino|della|di) ", low)),
        "es": len(re.findall(r" (y|para|con|una|el|los|las|mañana|hasta|del) ", low)),
        "en": len(re.findall(r" (and|for|with|the|a|tomorrow|find|near|from|to|in) ", low)),
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else fallback


def _score_domains(text: str) -> dict[Domain, float]:
    scores = {d: 0.0 for d in Domain}
    for d, cues in L.DOMAIN_CUES.items():
        for pat, w in cues:
            if re.search(pat, text, FLAGS):
                scores[d] += w
    # Holiday vs long-term apartment: dates or nights push towards STAY.
    if re.search(L.LONG_TERM_CUES, text, FLAGS) or (
        re.search(L.SALE_CUES, text, FLAGS)
        and re.search(r"apartment|flat|house|квартир|дом|wohnung|haus|appartement|maison|casa|piso", text, FLAGS)
    ):
        scores[Domain.REAL_ESTATE] += 2.5
        scores[Domain.STAY] -= 1.5
    return scores


def _pick_domain(scores: dict[Domain, float], text: str, has_route: bool) -> tuple[Domain | None, float]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top, s1), (_, s2) = ranked[0], ranked[1]
    if s1 <= 0:
        if has_route:
            return Domain.GROUND, 0.5
        return None, 0.0
    # Planning requests mention hotels, dinners and trains; the plan wins if it is explicit.
    if scores[Domain.PLAN] >= 2.5 and top is not Domain.PLAN:
        top, s1 = Domain.PLAN, scores[Domain.PLAN]
    # "cheapest way to travel A -> B, include flights, trains and buses" is multimodal ground.
    if (
        has_route
        and re.search(r"combinations?|any (mode|way)|way to (get|travel)|как (добраться|доехать)", text, FLAGS)
        and top in (Domain.FLIGHT, Domain.RAIL, Domain.GROUND)
    ):
        return Domain.GROUND, 0.9
    confidence = min(1.0, s1 / 4.0) * (1.0 if s1 - s2 >= 1.5 else 0.7)
    return top, round(confidence, 2)


def _categories(text: str, domain: Domain | None) -> list[str]:
    cats: list[str] = []
    for pat, token in L.CATEGORY_CUES:
        if re.search(pat, text, FLAGS) and token not in cats:
            cats.append(token)
    prefix_by_domain = {
        Domain.DINING: ("cuisine:", "place:cafe", "place:bar", "place:pub", "place:bakery", "place:brunch"),
        Domain.WELLNESS: ("place:",),
        Domain.EVENT: ("event:",),
        Domain.STAY: (
            "property:hotel",
            "property:hostel",
            "property:guesthouse",
            "property:apartment",
            "property:aparthotel",
        ),
        Domain.REAL_ESTATE: ("property:",),
        Domain.GROUND: ("mode:",),
        Domain.SHOPPING: ("place:",),
        Domain.PLAN: ("cuisine:", "event:"),
    }
    if domain is None:
        return cats
    allowed = prefix_by_domain.get(domain, ())
    kept = [c for c in cats if any(c.startswith(p) for p in allowed)]
    if domain is Domain.WELLNESS:
        kept = [c for c in kept if c not in ("place:cafe", "place:bar", "place:pub", "place:bakery", "place:brunch")]
    return kept


def _clauses(text: str) -> list[str]:
    parts = re.split(r"[.;!?\n]|,(?!\d{3})|\s+(?:but|and also|но|aber|mais|ma|pero)\s+", text)
    return [p for p in (s.strip() for s in parts) if p]


def _amount(raw: str) -> Decimal | None:
    s = raw.strip().lower().replace(" ", " ")
    mult = Decimal(1)
    if s.endswith("k"):
        mult, s = Decimal(1000), s[:-1]
    s = s.replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    elif re.fullmatch(r"\d+,\d{1,2}", s):
        s = s.replace(",", ".")
    try:
        return Decimal(s) * mult
    except ArithmeticError:
        return None


_CUR_ALT = "|".join(sorted((re.escape(k) for k in L.CURRENCY_TOKENS), key=len, reverse=True))
_NUM = r"\d{1,3}(?:[ ,. ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?k?"
_MONEY_RE = re.compile(
    rf"(?P<c1>{_CUR_ALT})\s?(?P<n1>{_NUM})(?!\w)|(?<![\w.])(?P<n2>{_NUM})\s?(?P<c2>{_CUR_ALT})(?!\w)", FLAGS
)


def _money_mentions(text: str) -> list[tuple[Money, int, int]]:
    out = []
    for m in _MONEY_RE.finditer(text):
        cur_tok = (m.group("c1") or m.group("c2") or "").lower()
        num = m.group("n1") or m.group("n2")
        code = L.CURRENCY_TOKENS.get(cur_tok)
        amt = _amount(num) if num else None
        if code and amt is not None and amt > 0:
            out.append((Money.of(amt, code), m.start(), m.end()))
    return out


def _budget(text: str, money: list[tuple[Money, int, int]], default_unit: BudgetUnit) -> Budget:
    if not money:
        return Budget(unit=default_unit)
    amount, s, e = money[0]
    unit = default_unit
    window = text[max(0, s - 30) : e + 40]
    for pat, u in L.BUDGET_UNIT_CUES:
        if re.search(pat, window, FLAGS):
            unit = BudgetUnit(u)
            break
    return Budget(max=amount, unit=unit)


def _party(text: str) -> tuple[Party | None, bool]:
    low = text.lower()
    words = "|".join(sorted((re.escape(w) for w in L.NUMBER_WORDS), key=len, reverse=True))
    n: int | None = None
    pats = [
        rf"\b(?:for|для|на|für|zu|pour|per|para)\s+(\d{{1,2}}|{words})(?:\s+(?:people|persons?|guests?|adults?|"
        rf"человек\w*|персон\w*|взрослы\w*|personen|erwachsene\w*|personnes?|adultes?|persone|adulti|personas|"
        rf"adultos))?\b",
        rf"\b(\d{{1,2}}|{words})\s+(?:people|persons?|guests?|adults?|pax|человек\w*|персон\w*|взрослы\w*|"
        rf"гост\w*|personen|erwachsene\w*|gäste|personnes?|adultes?|persone|adulti|ospiti|personas|adultos|"
        rf"huéspedes|հոգ\w*|մարդ\w*|ადამიან\w*|კაც\w*|адам\w*)\b",
        r"\b(?:вдво[её]м|zu zweit|à deux|in due|en pareja|as a couple|a couple)\b",
        rf"(\d{{1,2}}|{words})\s+(?:հոգու|անձի)\s+համար",
    ]
    explicit = False
    for i, p in enumerate(pats):
        m = re.search(p, low, FLAGS)
        if not m:
            continue
        if i == 2:
            n = 2
        else:
            tok = m.group(1)
            n = int(tok) if tok.isdigit() else L.NUMBER_WORDS.get(tok)
        if n:
            explicit = True
            break
    kids: list[int] = []
    for m in re.finditer(
        r"\b(?:child|kid|son|daughter|ребен\w*|реб[её]нк\w*|kind|enfant|bambin\w*|niñ\w*)\w*"
        r"\s*(?:\(|aged|age|of|возраст\w*|im alter von|de|di|años)?\s*(\d{1,2})",
        low,
    ):
        kids.append(int(m.group(1)))
    m = re.search(r"\b(\d)\s+(?:kids|children|детьми|детей|kinder\w*|enfants|bambini|niños)\b", low)
    if m and not kids:
        # Ages unknown: kept as -1 so providers that require ages force a clarification.
        kids = [-1] * int(m.group(1))
    if not explicit and not kids:
        return None, False
    adults = n if n else 2 if re.search(r"\bwe\b|\bмы\b|\bwir\b|\bnous\b|\bnoi\b|\bnosotros\b", low) else 1
    if kids and n and n > len(kids) and not re.search(r"adult|взросл|erwachsen", low):
        adults = max(1, n - len(kids))
    return Party(adults=max(1, min(adults, 30)), children_ages=kids), explicit


def _rooms(text: str) -> int | None:
    m = re.search(r"\b(\d)\s+(?:rooms|номер\w*|zimmer|chambres|camere|habitaciones)\b", text, FLAGS)
    return int(m.group(1)) if m else None


def _stem_variants(alias: str) -> list[str]:
    return [alias[:-1]] if len(alias) >= 5 and alias[-1] in "იаяеоыьa" else []


def _gazetteer_mentions(text: str) -> list[tuple[int, int, GazetteerEntry]]:
    found = GAZ.find_in_text(text)
    if found:
        return found
    # Inflected Georgian/Russian forms: თბილისში, Москве (stem + suffix).
    norm = normalize(text)
    out: list[tuple[int, int, GazetteerEntry]] = []
    for e in GAZ.entries:
        for alias in (e.name, *e.aliases):
            for stem in _stem_variants(normalize(alias)):
                for m in re.finditer(rf"(?<!\w){re.escape(stem)}\w{{1,4}}(?!\w)", norm):
                    if not any(not (m.end() <= s or m.start() >= en) for s, en, _ in out):
                        out.append((m.start(), m.end(), e))
    out.sort(key=lambda x: x[0])
    return out


_STOP_PROPER = (
    {n for names in MONTHS.values() for n in names}
    | {n for names in WEEKDAYS.values() for n in names}
    | {"i", "me", "we", "the", "a", "an", "find", "look", "please", "notify", "alert", "plan", "tomorrow", "today"}
)


def _regex_locations(text: str) -> list[tuple[int, str, str]]:
    """Capitalized place names after prepositions. Returns (pos, role, text)."""
    out: list[tuple[int, str, str]] = []
    latin_name = r"((?:[A-Z][\w'\-]+)(?:\s+(?:[A-Z][\w'\-]+|am|an|de|del|di|la|le|sur|upon)){0,3})"
    cyr_name = r"([А-ЯЁ][\w\-]+(?:\s+[А-ЯЁ][\w\-]+){0,2})"
    for role, words in (
        ("origin", _FROM_WORDS),
        ("dest", _TO_WORDS),
        (
            "in",
            r"(?:in|near|around|at|в|во|около|"
            r"рядом с|возле|bei|nahe|près de|vicino a|"
            r"cerca de|à|a)",
        ),
    ):
        for name_pat in (latin_name, cyr_name):
            for m in re.finditer(rf"(?<!\w){words}\s+{name_pat}", text):
                name = m.group(1).strip()
                first = name.split()[0].lower()
                if first in _STOP_PROPER or len(name) < 3:
                    continue
                out.append((m.start(1), role, name))
    # Armenian / Georgian / Kazakh case suffixes for "from".
    for m in re.finditer(r"([Ա-և]{3,})ից\b", text):
        out.append((m.start(1), "origin", m.group(1)))
    for m in re.finditer(r"([ა-ჿ]{3,})დან\b", text):
        out.append((m.start(1), "origin", m.group(1)))
    out.sort(key=lambda x: x[0])
    return out


def _role_of(text: str, pos: int) -> str | None:
    before = normalize(text[max(0, pos - 14) : pos])
    if re.search(rf"(?:^|\s){_FROM_WORDS}\s*$", before, FLAGS):
        return "origin"
    if re.search(rf"(?:^|\s){_TO_WORDS}\s*$", before, FLAGS):
        return "dest"
    return None


def _locations(text: str, domain: Domain | None) -> tuple[LocationRef | None, LocationRef | None, LocationRef | None]:
    """Returns (location, origin, destination)."""
    norm_text = normalize(text)
    mentions = _gazetteer_mentions(text)
    refs: list[tuple[int, str | None, LocationRef]] = []
    for s, e, entry in mentions:
        role = _role_of(norm_text, s)
        frag = norm_text[e : e + 4]
        if re.match(r"(ից|დან|нан|нен|дан|ден|тан|тен)", norm_text[s:e][-4:] + frag):
            role = "origin"
        refs.append((s, role, LocationRef(text=entry.name, place=entry.to_place())))
    if len(refs) < 2:
        seen = {r[2].text.lower() for r in refs}
        for pos, role, name in _regex_locations(text):
            if GAZ.lookup(name) or name.lower() in seen:
                continue
            refs.append((pos, None if role == "in" else role, LocationRef(text=name)))
            seen.add(name.lower())
        refs.sort(key=lambda x: x[0])

    is_route = domain is not None and (domain.is_transport or domain is Domain.PLAN)
    if domain is None:
        is_route = len(refs) >= 2 and any(r[1] == "origin" for r in refs)
    if not is_route:
        loc = refs[0][2] if refs else None
        return loc, None, None

    origin = next((r[2] for r in refs if r[1] == "origin"), None)
    dest = next((r[2] for r in refs if r[1] == "dest" and r[2] is not origin), None)
    rest = [r[2] for r in refs if r[2] is not origin and r[2] is not dest]
    if origin is None and dest is None and len(rest) >= 2:
        origin, dest = rest[0], rest[1]
    elif origin is None and dest is not None and rest:
        origin = rest[0]
    elif (dest is None and origin is not None and rest) or (origin is None and dest is None and len(rest) == 1):
        dest = rest[0]
    if (
        origin is not None
        and dest is not None
        and origin.place
        and dest.place
        and (origin.place.canonical_id == dest.place.canonical_id)
    ):
        origin = None
    if domain is Domain.PLAN:
        return dest or origin, origin if dest else None, dest
    return None, origin, dest


_RATING_RE = re.compile(
    r"\b(?:rating|rated|оценк\w*|рейтинг\w*|bewertung)\s*(?:of\s*|above\s*|from\s*|от\s*|ab\s*)?(\d(?:[.,]\d)?)\s*\+?"
)
_RATING_PLUS_RE = re.compile(r"\b(\d(?:[.,]\d)?)\s*\+\s*(?:rating|stars?|звезд\w*)")


def _numeric_constraints(text: str, intent: SearchIntent) -> None:
    low = text.lower()
    words = "|".join(sorted((re.escape(w) for w in L.NUMBER_WORDS), key=len, reverse=True))
    if m := re.search(
        rf"\b(\d|{words})[- ]?(?:bed(?:room)?s?|спал\w*|schlafzimmer|chambres?|camere da letto|"
        rf"dormitorios|habitaciones)\b",
        low,
    ):
        n = int(m.group(1)) if m.group(1).isdigit() else L.NUMBER_WORDS.get(m.group(1), 0)
        if n:
            intent.set_requirement("bedrooms.min", n)
    m = re.search(r"\b(\d)[- ]?(?:room|комнатн\w*|zimmer|pièces?|locali|ambientes)\b", low)
    if m and intent.domain is Domain.REAL_ESTATE:
        intent.set_requirement("rooms.min", int(m.group(1)))
    if m := re.search(r"\b(одно|двух|тр[её]х|четыр[её]х)комнатн\w*", low):
        intent.set_requirement(
            "rooms.min", {"одно": 1, "двух": 2, "трех": 3, "трёх": 3, "четырех": 4, "четырёх": 4}[m.group(1)]
        )
    if m := re.search(r"\b(\d{2,4})\s?(?:m2|m²|sqm|sq\.? ?m|м2|м²|кв\.? ?м)", low):
        intent.set_requirement("area.min", int(m.group(1)))
    m = _RATING_RE.search(low) or _RATING_PLUS_RE.search(low)
    if m:
        intent.set_requirement("rating.min", float(m.group(1).replace(",", ".")))
    if m := re.search(
        rf"\b(?:max(?:imum)?|at most|up to|не более|до|höchstens)\s*(\d|{words})\s*(?:stops?|"
        rf"transfers?|connections?|changes?|пересад\w*|umstieg\w*)",
        low,
    ):
        n = int(m.group(1)) if m.group(1).isdigit() else L.NUMBER_WORDS.get(m.group(1), 1)
        intent.set_requirement("max_transfers", n)
    if m := re.search(r"\b(business|first|premium economy|economy)\s*class\b|\b(бизнес|эконом)[- ]?класс\w*", low):
        cabin = (m.group(1) or m.group(2) or "economy").replace(" ", "_")
        cabin = {"бизнес": "business", "эконом": "economy"}.get(cabin, cabin)
        if intent.domain is Domain.FLIGHT:
            intent.set_requirement("cabin", cabin)
    if intent.domain is Domain.REAL_ESTATE:
        if re.search(L.SALE_CUES, low, FLAGS):
            intent.set_requirement("deal.sale")
        else:
            intent.set_requirement("deal.rent")


def _constraints(text: str, intent: SearchIntent) -> None:
    for clause in _clauses(text):
        pref = bool(re.search(L.PREFERENCE_MARKERS, clause, FLAGS))
        for pat, key, value in L.CONSTRAINT_CUES:
            if not re.search(pat, clause, FLAGS):
                continue
            cdef = CONSTRAINTS[key]
            if intent.domain not in cdef.domains:
                continue
            if key == "reservations" and intent.domain is not Domain.DINING:
                continue
            v = True if value is None else value
            if pref or cdef.preference_only:
                intent.set_preference(key, v)  # type: ignore[arg-type]
            else:
                intent.set_requirement(key, v)  # type: ignore[arg-type]
        for pat, key in L.ATMOSPHERE_CUES:
            if re.search(pat, clause, FLAGS) and intent.domain in CONSTRAINTS[key].domains:
                intent.set_preference(key)
    # A table for N is how people ask for a reservation; it is not a hard requirement on its own.
    if intent.has_requirement("reservations"):
        intent.drop("reservations")
        intent.set_preference("reservations")
    # Late arrival implies late check-in for stays.
    if intent.domain is Domain.STAY and intent.when and intent.when.start and intent.when.start.hour >= 22:
        intent.set_requirement("late_checkin")


def _monitor(text: str, money: list[tuple[Money, int, int]]) -> MonitorSpec | None:
    if not re.search(L.MONITOR_CUES, text, FLAGS) and not re.search(L.AVAILABILITY_CUES, text, FLAGS):
        return None
    if money and re.search(
        r"drops?|falls?|below|under|less than|cheaper|ниже|дешевле|упад\w*|опуст\w*|unter|"
        r"fällt|sous|baisse|scende|sotto|baja|por debajo",
        text,
        FLAGS,
    ):
        return MonitorSpec(kind=MonitorKind.PRICE_BELOW, threshold=money[0][0])
    if re.search(L.AVAILABILITY_CUES, text, FLAGS):
        return MonitorSpec(kind=MonitorKind.AVAILABILITY)
    if re.search(L.NEW_RESULTS_CUES, text, FLAGS):
        return MonitorSpec(kind=MonitorKind.NEW_RESULTS)
    if money:
        return MonitorSpec(kind=MonitorKind.PRICE_BELOW, threshold=money[0][0])
    return MonitorSpec(kind=MonitorKind.NEW_RESULTS)


def _window(dp: DateParse, zone: str | None, domain: Domain) -> DateWindow | None:
    if not dp.found:
        return None
    z = tz(zone)
    d1 = dp.date_from
    if d1 is None:
        return None
    start_t = dp.time_from or (time(0, 0) if domain is not Domain.DINING else None)
    if dp.arrive_by and dp.time_to:
        return DateWindow(start=datetime.combine(d1, time(0, 0), z), end=datetime.combine(d1, dp.time_to, z))
    start = datetime.combine(d1, start_t or time(0, 0), z)
    if dp.date_to and dp.date_to != d1:
        end = datetime.combine(dp.date_to, dp.time_to or time(23, 59), z)
    elif dp.time_to:
        end = datetime.combine(d1, dp.time_to, z)
    elif dp.time_from:
        # A point in time ("at 18:00") gets a small window; flexible search can widen it explicitly.
        end = start + timedelta(minutes=0)
    else:
        end = datetime.combine(d1, time(23, 59), z)
    return DateWindow(start=start, end=end)


def _required_missing(intent: SearchIntent) -> list[str]:
    d = intent.domain
    missing: list[str] = []
    if d.is_transport:
        if intent.origin is None:
            missing.append("origin")
        if intent.destination is None:
            missing.append("destination")
        if d in (Domain.FLIGHT, Domain.RAIL) and intent.when is None:
            missing.append("date")
    elif d is Domain.STAY:
        if intent.location is None:
            missing.append("location")
        if intent.stay is None:
            missing.append("dates")
    elif d is Domain.PLAN:
        if intent.location is None and intent.destination is None:
            missing.append("location")
        if intent.when is None:
            missing.append("dates")
    elif intent.location is None:
        missing.append("location")
    if d is Domain.STAY and any(a < 0 for a in intent.party.children_ages):
        missing.append("children_ages")
    return missing


def parse(
    text: str,
    *,
    today: date,
    zone: str | None = None,
    ui_language: str = "en",
    force_domain: Domain | None = None,
) -> ParseOutcome:
    lang = detect_language(text, ui_language)
    scores = _score_domains(text)
    _, pre_origin, pre_dest = _locations(text, None)
    has_route = pre_origin is not None and pre_dest is not None
    domain, confidence = _pick_domain(scores, text, has_route)
    if force_domain is not None:
        domain, confidence = force_domain, 1.0
    working_domain = domain or Domain.DINING

    location, origin, destination = _locations(text, working_domain)
    loc_for_tz = destination or location or origin
    zone_eff = (loc_for_tz.place.timezone if loc_for_tz and loc_for_tz.place else None) or zone
    if working_domain.is_transport and origin and origin.place and origin.place.timezone:
        zone_eff = origin.place.timezone

    intent = SearchIntent(domain=working_domain, raw_text=text, language=lang)
    intent.categories = _categories(text, domain)
    intent.location, intent.origin, intent.destination = location, origin, destination

    if location and re.search(
        r"\bnear\b|\baround\b|около|рядом|возле|\bbei\b|\bnahe\b|près de|vicino|cerca", text, FLAGS
    ):
        location.radius_m = 20_000
    for pat, token in L.NEAR_ANCHORS:
        if working_domain in (Domain.STAY, Domain.DINING, Domain.WELLNESS, Domain.REAL_ESTATE) and re.search(
            rf"(?:near|close to|next to|by|walking distance|около|рядом с|возле|у|nahe|beim?|près de|vicino a|"
            rf"cerca de|in the)\s+(?:the\s+)?(?:{pat})",
            text,
            FLAGS,
        ):
            intent.categories.append(token)

    dp = parse_dates(text, today)
    if working_domain is Domain.STAY and dp.date_from:
        check_out = (
            dp.date_to
            if dp.date_to and dp.date_to > dp.date_from
            else (dp.date_from + timedelta(days=dp.nights or 1) if dp.nights else None)
        )
        if check_out:
            intent.stay = StayDates(check_in=dp.date_from, check_out=check_out)
        if dp.time_from:
            intent.when = DateWindow(start=datetime.combine(dp.date_from, dp.time_from, tz(zone_eff)))
    else:
        intent.when = _window(dp, zone_eff, working_domain)
        intent.arrive_by = dp.arrive_by

    default_unit = {
        Domain.STAY: BudgetUnit.TOTAL,
        Domain.REAL_ESTATE: BudgetUnit.MONTH,
        Domain.DINING: BudgetUnit.TOTAL,
    }.get(working_domain, BudgetUnit.TOTAL)
    money = _money_mentions(text)
    intent.monitor = _monitor(text, money)
    budget_money = money
    if intent.monitor and intent.monitor.threshold and money and money[0][0] == intent.monitor.threshold:
        budget_money = money[1:]
    intent.budget = _budget(text, budget_money, default_unit)
    if intent.monitor and intent.monitor.threshold and intent.budget.max is None:
        intent.budget = Budget(max=intent.monitor.threshold, unit=intent.budget.unit)

    party, explicit = _party(text)
    if party:
        if rooms := _rooms(text):
            party.rooms = rooms
        intent.party = party
    if working_domain is Domain.DINING and not explicit and not party:
        intent.party = Party(adults=1)

    _constraints(text, intent)
    _numeric_constraints(text, intent)

    for pat, mode in L.SORT_CUES:
        if re.search(pat, text, FLAGS):
            intent.sort = SortMode(mode)
            break
    if intent.domain is Domain.PLAN:
        for pat, kind in L.PLAN_KIND_CUES:
            if re.search(pat, text, FLAGS):
                intent.plan_kind = PlanKind(kind)
                break
        if re.search(L.EXCLUDE_TRANSPORT, text, FLAGS):
            intent.categories.append("exclude:transport")
        if intent.plan_kind is PlanKind.ROMANTIC_WEEKEND:
            intent.set_preference("atmosphere.romantic")
        if intent.when is None and intent.plan_kind is PlanKind.ROMANTIC_WEEKEND:
            sat = today + timedelta(days=(5 - today.weekday()) % 7)
            z = tz(zone_eff)
            intent.when = DateWindow(
                start=datetime.combine(sat, time(10), z), end=datetime.combine(sat + timedelta(days=1), time(20), z)
            )
    if intent.domain is Domain.GROUND and not any(c.startswith("mode:") for c in intent.categories):
        intent.categories.append("mode:multimodal")

    intent.requirements = [c for c in intent.requirements if intent.domain in c.definition.domains]
    intent.preferences = [c for c in intent.preferences if intent.domain in c.definition.domains]
    intent.missing = _required_missing(intent)
    if domain is None:
        intent.missing.insert(0, "domain")
    return ParseOutcome(intent=intent, confidence=confidence, domain_scores=scores)
