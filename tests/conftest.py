from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

# Tests never use real credentials or a real database.
os.environ.setdefault("RADAR_ENV", "test")
os.environ["RADAR_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
# Fresh on-disk cache per test session so cached upstream answers never leak between runs.
os.environ["RADAR_CACHE_DIR"] = tempfile.mkdtemp(prefix="radar-test-cache-")
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
