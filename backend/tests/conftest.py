"""Shared test fixtures.

Tests run against a real PostgreSQL database (no SQLite stand-in), so what passes
here is what runs in Docker and CI. Point them at a database with:

    export NEXUS_TEST_DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test
"""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "NEXUS_TEST_DATABASE_URL",
    "postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test",
)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": TEST_DATABASE_URL,
        "log_json": False,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # Run the app's lifespan so app.state.db exists, exactly as under uvicorn.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client_for(create_app(settings)):
        yield c


@pytest.fixture
def client_factory():
    """Build a client for an app with custom settings or extra routes."""

    def factory(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
        return _client_for(app)

    return factory
