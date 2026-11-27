"""Recognise a bare currency conversion ("100 usd", "50 eur to amd", "$20 в драмах").

Deliberately strict: the whole message has to be a conversion, so a search
such as "hotel under 100 eur" never ends up here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from babel.numbers import list_currencies

from radar.nlu import lexicon as L
from radar.nlu.rules import _amount

_ISO = frozenset(list_currencies())
_SYMBOLS = "".join(re.escape(k) for k in L.CURRENCY_TOKENS if len(k) == 1)
_NUM = r"\d{1,3}(?:[ ,. ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?k?"
_WORD = rf"[{_SYMBOLS}]|[^\W\d_]{{2,12}}\.?"
_LEAD = (
    r"(?:convert|exchange|how much is|конвертир\w*|переведи|сколько будет|сколько|"
    r"umrechnen|convertir|converti|quanto fa)?"
)
_JOIN = r"(?:to|in|into|->|=|в|во|на|in|nach|en|à|a|in)"
_RE = re.compile(
    rf"^\s*{_LEAD}\s*(?:(?P<c1>{_WORD})\s?(?P<n1>{_NUM})|(?P<n2>{_NUM})\s?(?P<c2>{_WORD}))"
    rf"(?:\s*{_JOIN}?\s*(?P<q>{_WORD}))?\s*[?!.]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Conversion:
    amount: Decimal
    base: str
    quote: str | None


def currency(word: str | None) -> str | None:
    if not word:
        return None
    w = word.strip().rstrip(".").lower()
    if w in L.CURRENCY_TOKENS:
        return L.CURRENCY_TOKENS[w]
    if len(w) == 3 and w.upper() in _ISO:
        return w.upper()
    # Inflected forms: "драмах", "долларах", "евро", "dollari".
    best = max(
        (k for k in L.CURRENCY_TOKENS if len(k) >= 4 and w.startswith(k[: max(4, len(k) - 1)])),
        key=len,
        default=None,
    )
    return L.CURRENCY_TOKENS[best] if best else None


def parse_conversion(text: str) -> Conversion | None:
    m = _RE.match(text or "")
    if not m:
        return None
    n = m.group("n1") or m.group("n2")
    base = currency(m.group("c1") or m.group("c2"))
    if base is None or n is None:
        return None
    amount = _amount(n)
    if amount is None or amount <= 0:
        return None
    q_raw = m.group("q")
    quote = currency(q_raw) if q_raw else None
    if q_raw and quote is None:
        return None  # "100 eur hotel" is not a conversion
    return Conversion(amount=amount, base=base, quote=quote if quote != base else None)
