from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from radar.storage.models import Base


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, object] = {"pool_pre_ping": True}
        if url.startswith("sqlite") and ":memory:" in url:
            # One shared connection so every session sees the same in-memory database.
            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    async def create_all(self) -> None:
        """Development and tests only; production uses Alembic migrations."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()
