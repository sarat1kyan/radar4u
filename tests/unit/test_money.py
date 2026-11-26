from datetime import UTC, datetime
from decimal import Decimal

import pytest

from radar.core.money import FxRate, Money, MoneyError


def test_rejects_float():
    with pytest.raises((MoneyError, ValueError)):
        Money.of(0.1, "EUR")


def test_decimal_arithmetic_is_exact():
    a = Money.of("0.10", "EUR") + Money.of("0.20", "EUR")
    assert a.amount == Decimal("0.30")


def test_currency_mismatch():
    with pytest.raises(MoneyError):
        _ = Money.of(1, "EUR") + Money.of(1, "USD")


def test_quantize_uses_currency_minor_units():
    assert str(Money.of("1234.567", "EUR").quantized().amount) == "1234.57"
    assert str(Money.of("1234.5", "JPY").quantized().amount) == "1234"


def test_conversion_keeps_original():
    rate = FxRate("EUR", "AMD", Decimal("430.5"), datetime(2026, 11, 26, tzinfo=UTC), "ecb")
    c = rate.convert(Money.of("10", "EUR"))
    assert c.original.amount == Decimal("10")
    assert c.converted.currency == "AMD"
    assert c.converted.amount == Decimal("4305.00")
    assert c.as_dict()["rate_source"] == "ecb"


def test_json_roundtrip_is_string():
    m = Money.of("19.99", "usd")
    dumped = m.model_dump(mode="json")
    assert dumped == {"amount": "19.99", "currency": "USD"}
    assert Money.model_validate(dumped) == m
