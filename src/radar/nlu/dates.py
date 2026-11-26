"""Relative and absolute date/time extraction in nine languages.

All results are relative to a caller-supplied `today` (the local date of the
search location, not the server), so "tomorrow" in Yerevan and Lisbon can
legitimately differ.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

MONTHS: dict[int, tuple[str, ...]] = {
    1: (
        "january",
        "jan",
        "января",
        "январь",
        "январе",
        "հունվար",
        "იანვ",
        "қаңтар",
        "januar",
        "janvier",
        "gennaio",
        "enero",
    ),
    2: (
        "february",
        "feb",
        "февраля",
        "февраль",
        "феврале",
        "փետրվար",
        "თებერვ",
        "ақпан",
        "februar",
        "février",
        "febbraio",
        "febrero",
    ),
    3: ("march", "mar", "марта", "март", "марте", "մարտ", "მარტ", "наурыз", "märz", "mars", "marzo"),
    4: ("april", "apr", "апреля", "апрель", "апреле", "ապրիլ", "აპრილ", "сәуір", "avril", "aprile", "abril"),
    5: ("may", "мая", "май", "мае", "մայիս", "მაის", "мамыр", "mai", "maggio", "mayo"),
    6: ("june", "jun", "июня", "июнь", "июне", "հունիս", "ივნის", "маусым", "juni", "juin", "giugno", "junio"),
    7: ("july", "jul", "июля", "июль", "июле", "հուլիս", "ივლის", "шілде", "juli", "juillet", "luglio", "julio"),
    8: ("august", "aug", "августа", "август", "августе", "օգոստոս", "აგვისტ", "тамыз", "août", "agosto"),
    9: (
        "september",
        "sep",
        "sept",
        "сентября",
        "сентябрь",
        "сентябре",
        "սեպտեմբեր",
        "სექტემბ",
        "қыркүйек",
        "septembre",
        "settembre",
        "septiembre",
    ),
    10: (
        "october",
        "oct",
        "октября",
        "октябрь",
        "октябре",
        "հոկտեմբեր",
        "ოქტომბ",
        "қазан",
        "oktober",
        "octobre",
        "ottobre",
        "octubre",
    ),
    11: ("november", "nov", "ноября", "ноябрь", "ноябре", "նոյեմբեր", "ნოემბ", "қараша", "novembre", "noviembre"),
    12: (
        "december",
        "dec",
        "декабря",
        "декабрь",
        "декабре",
        "դեկտեմբեր",
        "დეკემბ",
        "желтоқсан",
        "dezember",
        "décembre",
        "dicembre",
        "diciembre",
    ),
}
_MONTH_LOOKUP: list[tuple[str, int]] = sorted(
    ((name, m) for m, names in MONTHS.items() for name in names), key=lambda x: -len(x[0])
)
_MONTH_ALT = "|".join(re.escape(n) for n, _ in _MONTH_LOOKUP)

WEEKDAYS: dict[int, tuple[str, ...]] = {
    0: ("monday", "понедельник", "երկուշաբթի", "ორშაბათ", "дүйсенбі", "montag", "lundi", "lunedì", "lunes"),
    1: ("tuesday", "вторник", "երեքշաբթի", "სამშაბათ", "сейсенбі", "dienstag", "mardi", "martedì", "martes"),
    2: ("wednesday", "сред", "չորեքշաբթի", "ოთხშაბათ", "сәрсенбі", "mittwoch", "mercredi", "mercoledì", "miércoles"),
    3: ("thursday", "четверг", "հինգշաբթի", "ხუთშაბათ", "бейсенбі", "donnerstag", "jeudi", "giovedì", "jueves"),
    4: ("friday", "пятниц", "ուրբաթ", "პარასკევ", "жұма", "freitag", "vendredi", "venerdì", "viernes"),
    5: ("saturday", "суббот", "շաբաթ", "შაბათ", "сенбі", "samstag", "samedi", "sabato", "sábado"),
    6: ("sunday", "воскресен", "կիրակի", "კვირა", "жексенбі", "sonntag", "dimanche", "domenica", "domingo"),
}

TODAY = r"\btoday\b|\btonight\b|сегодня|այսօր|დღეს|бүгін|\bheute\b|\baujourd'hui\b|\bce soir\b|\boggi\b|\bstasera\b|\bhoy\b|\besta noche\b"
TOMORROW = r"\btomorrow\b|завтра|վաղը|ხვალ|ертең|\bmorgen\b|\bdemain\b|\bdomani\b|\bmañana\b"
DAY_AFTER = (
    r"\bday after tomorrow\b|послезавтра|վաղը չէ մյուս օրը|ზეგ|\bübermorgen\b|\baprès-demain\b|\bdopodomani\b|"
    r"\bpasado mañana\b"
)
WEEKEND = r"\bweekend\b|выходн\w*|հանգստյան օր\w*|შაბათ-კვირა\w*|демалыс\w*|\bwochenende\b|\bweek-?end\b|\bfine settimana\b|\bfin de semana\b"
NEXT_WEEK = r"\bnext week\b|на следующей неделе|след\w* недел\w*|\bnächste woche\b|\bla semaine prochaine\b|\bla prossima settimana\b|\bla próxima semana\b|\bla semana que viene\b"
NEXT_MONTH = r"\bnext month\b|в следующем месяце|след\w* месяц\w*|հաջորդ ամս\w*|შემდეგ თვე\w*|келесі ай\w*|\bnächsten monat\b|\ble mois prochain\b|\bil mese prossimo\b|\bmese prossimo\b|\bel próximo mes\b|\bel mes que viene\b"
THIS_WEEK = r"\bthis week\b|на этой неделе|\bdiese woche\b|\bcette semaine\b|\bquesta settimana\b|\besta semana\b"

ARRIVE_BY = (
    r"\barriv\w*\s+(?:by|before|no later than)\b|\bbe (?:there|in \w+) by\b|\bприбы\w*\s+до\b|\bприезд\w*\s+до\b|"
    r"\bприлет\w*\s+до\b|\bприлёт\w*\s+до\b|\bankunft\s+(?:vor|bis)\b|\bankommen\s+(?:vor|bis)\b|"
    r"\barriv\w*\s+avant\b|\barriv\w*\s+prima\b|\bllegar\w*\s+antes\b|\bllegada\s+antes\b"
)
BEFORE = r"\bbefore\b|\bby\b|\bno later than\b|\bдо\b|\bvor\b|\bbis\b|\bavant\b|\bprima delle?\b|\bantes de\b"
AFTER = r"\bafter\b|\bпосле\b|\bnach\b|\baprès\b|\bdopo\b|\bdespués de\b|\bfrom\b|\bс\b"

_TIME_RE = re.compile(
    r"(?<![\d.:/])(?P<h>[01]?\d|2[0-3])(?:[:h](?P<m>[0-5]\d))\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?(?![\d:/]|\.\d)"
    r"|(?<![\d.:/])(?P<h2>1[0-2]|0?[1-9])\s*(?P<ap2>am|pm|a\.m\.|p\.m\.)(?!\w)"
    r"|(?<![\d.:/])(?P<h3>[01]?\d|2[0-3])\s*(?:uhr|h|час\w*|ժ\.?)(?!\w)",
    re.IGNORECASE,
)


@dataclass
class DateParse:
    date_from: date | None = None
    date_to: date | None = None
    time_from: time | None = None
    time_to: time | None = None
    arrive_by: bool = False
    nights: int | None = None
    month_window: bool = False
    spans: list[tuple[int, int]] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.date_from is not None or self.time_from is not None or self.time_to is not None


def _month_of(token: str) -> int | None:
    t = token.lower().rstrip(".")
    for name, m in _MONTH_LOOKUP:
        if t.startswith(name) and (len(name) >= 4 or t == name):
            return m
    return None


def _roll_year(today: date, month: int, day: int) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


def _to_time(h: int, m: int, ap: str | None) -> time:
    if ap:
        ap = ap.lower().replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
    return time(h % 24, m)


def find_times(text: str) -> list[tuple[time, int, int]]:
    out = []
    for m in _TIME_RE.finditer(text):
        if m.group("h") is not None:
            t = _to_time(int(m.group("h")), int(m.group("m") or 0), m.group("ap"))
        elif m.group("h2") is not None:
            t = _to_time(int(m.group("h2")), 0, m.group("ap2"))
        else:
            t = time(int(m.group("h3")), 0)
        out.append((t, m.start(), m.end()))
    return out


def _next_weekday(today: date, wd: int, strictly_next: bool = False) -> date:
    delta = (wd - today.weekday()) % 7
    if delta == 0 and strictly_next:
        delta = 7
    return today + timedelta(days=delta)


def parse_dates(text: str, today: date) -> DateParse:
    res = DateParse()
    low = text.lower()
    m: re.Match[str] | None

    def mark(m: re.Match[str]) -> None:
        res.spans.append(m.span())

    # ISO dates first, they are unambiguous.
    iso = list(re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", low))
    dates: list[tuple[int, date]] = []
    for m in iso:
        try:
            dates.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
            mark(m)
        except ValueError:
            continue

    # "21-27 December", "21 to 27 December", "с 21 по 27 декабря"
    rng = re.search(
        rf"\b(\d{{1,2}})\s*(?:-|–|to|till|until|по|до|bis|au|al|a)\s*(\d{{1,2}})\s*(?:of\s+)?({_MONTH_ALT})\w*",
        low,
    )
    if rng:
        mo = _month_of(rng.group(3))
        if mo:
            d1 = _roll_year(today, mo, int(rng.group(1)))
            if d1:
                try:
                    d2 = date(d1.year, mo, int(rng.group(2)))
                    dates += [(rng.start(), d1), (rng.start() + 1, d2)]
                    mark(rng)
                except ValueError:
                    pass
    # "December 21-27"
    rng2 = re.search(rf"\b({_MONTH_ALT})\w*\.?\s+(\d{{1,2}})\s*(?:-|–|to|till|until)\s*(\d{{1,2}})\b", low)
    if rng2 and not rng:
        mo = _month_of(rng2.group(1))
        if mo:
            d1 = _roll_year(today, mo, int(rng2.group(2)))
            if d1:
                try:
                    d2 = date(d1.year, mo, int(rng2.group(3)))
                    dates += [(rng2.start(), d1), (rng2.start() + 1, d2)]
                    mark(rng2)
                except ValueError:
                    pass

    taken = list(res.spans)

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in taken)

    # "21 December", "21. Dezember", "21 декабря"
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th|\.|-?е|-?го)?\s+(?:of\s+|de\s+)?({_MONTH_ALT})\w*", low):
        if not free(*m.span()):
            continue
        mo = _month_of(m.group(2))
        if mo and (d := _roll_year(today, mo, int(m.group(1)))):
            dates.append((m.start(), d))
            taken.append(m.span())
            mark(m)
    # "December 21", "Dec 21st"
    for m in re.finditer(rf"\b({_MONTH_ALT})\w*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*[:h]\d)", low):
        if not free(*m.span()):
            continue
        mo = _month_of(m.group(1))
        if mo and (d := _roll_year(today, mo, int(m.group(2)))):
            dates.append((m.start(), d))
            taken.append(m.span())
            mark(m)
    # 21.12 / 21/12 / 21.12.2026 (day-first, which is the convention in every launch market except the US)
    for m in re.finditer(r"(?<![\d:.,])(\d{1,2})[./](\d{1,2})(?:[./](20\d\d))?(?![\d:+k])", low):
        if not free(*m.span()):
            continue
        dd, mm = int(m.group(1)), int(m.group(2))
        if not m.group(3) and len(m.group(1)) == 1 and len(m.group(2)) == 1:
            continue  # "4.5" is far more often a rating or a decimal than a date
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            continue
        try:
            d = date(int(m.group(3)), mm, dd) if m.group(3) else _roll_year(today, mm, dd)
        except ValueError:
            d = None
        if d:
            dates.append((m.start(), d))
            taken.append(m.span())
            mark(m)

    if not dates:
        if m := re.search(DAY_AFTER, low):
            dates.append((m.start(), today + timedelta(days=2)))
            mark(m)
        elif m := re.search(TOMORROW, low):
            dates.append((m.start(), today + timedelta(days=1)))
            mark(m)
        elif m := re.search(TODAY, low):
            dates.append((m.start(), today))
            mark(m)

    if not dates:
        for wd, names in WEEKDAYS.items():
            pat = "|".join(re.escape(n) for n in names)
            if m := re.search(rf"(?<!\w)(?:next\s+|следующ\w*\s+|nächsten?\s+)?(?:{pat})\w*", low):
                strictly = bool(re.match(r"next|следующ|nächst", m.group(0)))
                dates.append((m.start(), _next_weekday(today, wd, strictly)))
                mark(m)
                break

    if not dates and (m := re.search(WEEKEND, low)):
        if today.weekday() == 6:
            start, end = today, today
        else:
            start = _next_weekday(today, 5)
            end = start + timedelta(days=1)
        dates += [(m.start(), start), (m.start() + 1, end)]
        mark(m)

    if not dates and (m := re.search(NEXT_MONTH, low)):
        y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        last = calendar.monthrange(y, mo)[1]
        dates += [(m.start(), date(y, mo, 1)), (m.start() + 1, date(y, mo, last))]
        res.month_window = True
        mark(m)
    elif not dates and (m := re.search(NEXT_WEEK, low)):
        monday = _next_weekday(today, 0, strictly_next=True)
        dates += [(m.start(), monday), (m.start() + 1, monday + timedelta(days=6))]
        mark(m)
    elif not dates and (m := re.search(THIS_WEEK, low)):
        dates += [(m.start(), today), (m.start() + 1, today + timedelta(days=6 - today.weekday()))]
        mark(m)
    elif not dates and (
        m := re.search(rf"(?:\bin\b|\bв\b|\bim\b|\ben\b|\bà\b|\ba\b|\bdi\b)\s+({_MONTH_ALT})\w*\b", low)
    ):
        # "in December", "в декабре"
        mo = _month_of(m.group(1))
        if mo:
            y = today.year if mo >= today.month else today.year + 1
            start = date(y, mo, 1) if (y, mo) != (today.year, today.month) else today
            dates += [(m.start(), start), (m.start() + 1, date(y, mo, calendar.monthrange(y, mo)[1]))]
            res.month_window = True
            mark(m)

    dates.sort(key=lambda x: x[0])
    if dates:
        res.date_from = dates[0][1]
        if len(dates) > 1 and dates[-1][1] >= dates[0][1]:
            res.date_to = dates[-1][1]

    # nights
    if m := re.search(r"\b(\d{1,2})\s*(?:nights?|ноч\w*|nächte|nacht|nuits?|notti|noches?|գիշեր|ღამე)", low):
        res.nights = int(m.group(1))
        mark(m)
        if res.date_from and not res.date_to:
            res.date_to = res.date_from + timedelta(days=res.nights)

    # times
    times = [(t, s, e) for t, s, e in find_times(text) if free(s, e)]
    if times:
        between = re.search(r"\bbetween\b|\bмежду\b|\bс\b.*\bдо\b|\bzwischen\b|\bentre\b|\btra\b|\bfra\b", low)
        if len(times) >= 2 and between:
            res.time_from, res.time_to = times[0][0], times[1][0]
        else:
            t, s, _ = times[0]
            prefix = low[max(0, s - 40) : s]
            if re.search(ARRIVE_BY, prefix):
                res.arrive_by = True
                res.time_to = t
            elif re.search(rf"(?:{BEFORE})\s*$", prefix.strip() + " ") or re.search(rf"(?:{BEFORE})\s*$", prefix):
                res.time_to = t
            elif re.search(rf"(?:{AFTER})\s*$", prefix):
                res.time_from = t
            else:
                res.time_from = t
        for _, s, e in times[:2]:
            res.spans.append((s, e))
    elif re.search(r"\bmidnight\b|полноч\w*|\bmitternacht\b|\bminuit\b|\bmezzanotte\b|\bmedianoche\b", low):
        res.time_from = time(0, 0)
    return res
