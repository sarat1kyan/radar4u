"""Monetary amounts.

Amounts are Decimal plus an ISO 4217 code. Floats are rejected at construction
time so they cannot leak in from JSON parsing or arithmetic by accident.
Converted amounts never replace the original: a ConvertedMoney keeps the source
amount, the rate, and when/where the rate came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from babel.numbers import get_currency_precision
from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


class MoneyError(ValueError):
    pass


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise MoneyError("float amounts are not accepted; pass str, int or Decimal")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int | str):
        try:
            d = Decimal(str(value).strip().replace(" ", "").replace(" ", ""))
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal amount: {value!r}") from exc
    else:
        raise MoneyError(f"unsupported amount type: {type(value).__name__}")
    if not d.is_finite():
        raise MoneyError("amount must be finite")
    return d


def normalize_currency(code: str) -> str:
    c = code.strip().upper()
    if len(c) != 3 or not c.isalpha():
        raise MoneyError(f"not an ISO 4217 code: {code!r}")
    return c


def minor_units(currency: str) -> int:
    try:
        return int(get_currency_precision(currency))
    except Exception:  # unknown code in CLDR, fall back to 2
        return 2


class Money(BaseModel):
    """Immutable decimal amount in a single currency."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: str

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v: object) -> Decimal:
        return _to_decimal(v)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v: object) -> str:
        if not isinstance(v, str):
            raise MoneyError("currency must be a string")
        return normalize_currency(v)

    @field_serializer("amount")
    def _ser_amount(self, v: Decimal) -> str:
        return str(v)

    @classmethod
    def of(cls, amount: object, currency: str) -> Money:
        return cls(amount=_to_decimal(amount), currency=currency)

    def quantized(self) -> Money:
        exp = Decimal(1).scaleb(-minor_units(self.currency))
        return Money(amount=self.amount.quantize(exp, rounding=ROUND_HALF_EVEN), currency=self.currency)

    def _check(self, other: Money) -> None:
        if other.currency != self.currency:
            raise MoneyError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, k: int | Decimal) -> Money:
        if isinstance(k, float):
            raise MoneyError("multiply by int or Decimal only")
        return Money(amount=self.amount * Decimal(k), currency=self.currency)

    def __truediv__(self, k: int | Decimal) -> Money:
        if isinstance(k, float):
            raise MoneyError("divide by int or Decimal only")
        return Money(amount=self.amount / Decimal(k), currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    def __str__(self) -> str:
        q = self.quantized()
        return f"{q.amount} {q.currency}"


@dataclass(frozen=True, slots=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal  # 1 base = rate quote
    as_of: datetime
    source: str

    def convert(self, money: Money) -> ConvertedMoney:
        if money.currency != self.base:
            raise MoneyError(f"rate is for {self.base}, got {money.currency}")
        converted = Money(amount=money.amount * self.rate, currency=self.quote).quantized()
        return ConvertedMoney(original=money, converted=converted, rate=self)


@dataclass(frozen=True, slots=True)
class ConvertedMoney:
    original: Money
    converted: Money
    rate: FxRate

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original.model_dump(mode="json"),
            "converted": self.converted.model_dump(mode="json"),
            "rate": str(self.rate.rate),
            "rate_as_of": self.rate.as_of.isoformat(),
            "rate_source": self.rate.source,
        }
