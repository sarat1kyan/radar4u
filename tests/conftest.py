from __future__ import annotations

import os
from datetime import date

import pytest

# Tests never use real credentials or a real database.
os.environ.setdefault("RADAR_ENV", "test")
os.environ["RADAR_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
for var in (
    "DUFFEL_ACCESS_TOKEN",
    "TRAVELPAYOUTS_TOKEN",
    "LITEAPI_KEY",
    "TICKETMASTER_API_KEY",
):
    os.environ.pop(var, None)

# Thursday. Fixed so relative dates are deterministic.
TODAY = date(2026, 11, 26)


@pytest.fixture
def today() -> date:
    return TODAY
