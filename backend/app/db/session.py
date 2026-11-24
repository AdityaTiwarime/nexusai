"""Async database engine and session management."""

import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


class Database:
    """Owns the engine and session factory for one application instance."""

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            str(settings.database_url),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._health_timeout = settings.db_health_timeout_seconds

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> None:
        """Raise if the database cannot answer `SELECT 1` within the health timeout."""
        async with asyncio.timeout(self._health_timeout):
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
