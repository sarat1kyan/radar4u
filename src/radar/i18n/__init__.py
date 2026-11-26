"""Message catalogs and locale-aware formatting.

Catalogs are flat JSON files in i18n/locales/<lang>.json. Adding a language is
dropping in a file; no code changes. Strings use {name} placeholders and an
ICU subset for plurals: {count, plural, =0 {...} one {# item} few {...} other {# items}}
with CLDR plural rules from Babel. Missing keys fall back to English, then to
the key itself, so a half-translated catalog never breaks the bot.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from babel import Locale
from babel.dates import format_date, format_datetime, format_time
from babel.numbers import format_currency, format_decimal

from radar.core.money import Money

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT = "en"


@lru_cache
def available() -> dict[str, str]:
    """language code -> native name"""
    out = {}
    for f in sorted(LOCALES_DIR.glob("*.json")):
        meta = json.loads(f.read_text(encoding="utf-8")).get("_meta", {})
        out[f.stem] = meta.get("native", f.stem)
    return out


@lru_cache
def catalog(lang: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, str)}


def normalize_lang(code: str | None) -> str:
    if not code:
        return DEFAULT
    base = code.replace("_", "-").split("-")[0].lower()
    return base if base in available() else DEFAULT


@lru_cache
def _locale(lang: str) -> Locale:
    try:
        return Locale.parse(lang)
    except Exception:
        return Locale.parse(DEFAULT)


def _find_plural(s: str, start: int) -> tuple[int, int] | None:
    """Return (start, end) of a balanced {...} block starting at `start`."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    return None


_PLURAL_HEAD = re.compile(r"\{(\w+),\s*plural,")
_FORM = re.compile(r"\s*(=\d+|zero|one|two|few|many|other)\s*\{")


def _render_plural(block: str, value: Any, lang: str) -> str:
    head = _PLURAL_HEAD.match(block)
    assert head
    body = block[head.end() : -1]
    forms: dict[str, str] = {}
    pos = 0
    while (m := _FORM.match(body, pos)) is not None:
        span = _find_plural(body, m.end() - 1)
        if span is None:
            break
        forms[m.group(1)] = body[span[0] + 1 : span[1] - 1]
        pos = span[1]
    try:
        n = Decimal(str(value))
    except Exception:
        n = Decimal(0)
    exact = f"={n.normalize():f}" if n == n.to_integral_value() else None
    if exact and exact in forms:
        chosen = forms[exact]
    else:
        category = _locale(lang).plural_form(n)
        chosen = forms.get(category) or forms.get("other", "")
    return chosen.replace("#", format_decimal(n, locale=_locale(lang)))


def format_message(template: str, lang: str, **params: Any) -> str:
    out = template
    for m in list(_PLURAL_HEAD.finditer(template))[::-1]:
        span = _find_plural(template, m.start())
        if span is None:
            continue
        name = m.group(1)
        rendered = _render_plural(template[span[0] : span[1]], params.get(name, 0), lang)
        out = out[: span[0]] + rendered + out[span[1] :]
    return re.sub(r"\{(\w+)\}", lambda m: str(params.get(m.group(1), m.group(0))), out)


def t(key: str, lang: str = DEFAULT, **params: Any) -> str:
    lang = normalize_lang(lang)
    template = catalog(lang).get(key) or catalog(DEFAULT).get(key) or key
    return format_message(template, lang, **params)


def has(key: str) -> bool:
    return key in catalog(DEFAULT)


def money(m: Money, lang: str) -> str:
    q = m.quantized()
    try:
        return format_currency(q.amount, q.currency, locale=_locale(normalize_lang(lang)))
    except Exception:
        return f"{q.amount} {q.currency}"


def number(v: float | int | Decimal, lang: str, fmt: str | None = None) -> str:
    return format_decimal(v, format=fmt, locale=_locale(normalize_lang(lang)))


def fdate(d: date | datetime, lang: str, fmt: str = "medium") -> str:
    return format_date(d, format=fmt, locale=_locale(normalize_lang(lang)))


def ftime(t_: time | datetime, lang: str, clock: str = "24h") -> str:
    pattern = "HH:mm" if clock == "24h" else "h:mm a"
    return format_time(t_, format=pattern, locale=_locale(normalize_lang(lang)))


def fdatetime(dt: datetime, lang: str, clock: str = "24h") -> str:
    pattern = "EEE d MMM, HH:mm" if clock == "24h" else "EEE d MMM, h:mm a"
    return format_datetime(dt, format=pattern, tzinfo=dt.tzinfo, locale=_locale(normalize_lang(lang)))


def distance(meters: float, lang: str) -> str:
    if meters < 1000:
        return f"{number(round(meters / 10) * 10, lang)} m"
    return f"{number(round(meters / 1000, 1), lang)} km"


def age(seconds: float, lang: str) -> str:
    if seconds < 60:
        return t("common.just_now", lang)
    if seconds < 3600:
        return t("common.minutes_ago", lang, count=int(seconds // 60))
    if seconds < 86400:
        return t("common.hours_ago", lang, count=int(seconds // 3600))
    return t("common.days_ago", lang, count=int(seconds // 86400))
