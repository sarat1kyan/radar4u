import os

import pytest
from sqlalchemy import text

from radar.config import Settings
from radar.providers.base import AccessLevel
from radar.providers.fixtures import FixtureProvider
from radar.providers.registry import ProviderRegistry
from radar.search.pipeline import SearchPipeline
from radar.storage.db import Database


@pytest.fixture
def settings():
    return Settings(RADAR_ENV="test", RADAR_ENABLE_FIXTURES=True, RADAR_MIN_RADAR_INTERVAL=600)


@pytest.fixture
async def db():
    # Set RADAR_TEST_DATABASE_URL to run the same tests against PostgreSQL.
    url = os.environ.get("RADAR_TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    d = Database(url)
    if d.is_postgres:
        async with d.engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    await d.create_all()
    yield d
    await d.dispose()


@pytest.fixture
def fixtures():
    return FixtureProvider()


@pytest.fixture
def registry(fixtures, settings):
    return ProviderRegistry([fixtures], settings)


@pytest.fixture
def pipeline(registry):
    return SearchPipeline(registry, timeout_s=5)


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send(self, telegram_id, text, buttons=None):
        self.sent.append((telegram_id, text, buttons))
        return True


@pytest.fixture
def sender():
    return FakeSender()


__all__ = ["AccessLevel"]
