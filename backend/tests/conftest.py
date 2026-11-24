"""Shared test fixtures.

Tests run against a real PostgreSQL database (no SQLite stand-in), so what passes
here is what runs in Docker and CI. Point them at a database with:

    export NEXUS_TEST_DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test

The schema is created once per test run with the real Alembic migrations, and
every table is emptied after each test so tests never depend on each other.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "NEXUS_TEST_DATABASE_URL",
    "postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai_test",
)
BACKEND_DIR = Path(__file__).resolve().parents[1]
API = "/api/v1"
PASSWORD = "Correct-Horse-Battery-9"
TABLES = ["audit_logs", "refresh_tokens", "users", "organizations"]


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": TEST_DATABASE_URL,
        "log_json": False,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NEXUS_DATABASE_URL": TEST_DATABASE_URL}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, f"migrations failed:\n{result.stderr}"


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    yield
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            exists = await conn.scalar(text("SELECT to_regclass('organizations') IS NOT NULL"))
            if exists:
                await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


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


# --- auth helpers ---------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Account:
    token: str
    email: str
    user_id: str
    organization_id: str | None


async def register_org(
    client: httpx.AsyncClient, org: str = "Acme Corp", email: str = "admin@acme.com"
) -> Account:
    response = await client.post(
        f"{API}/auth/register",
        json={
            "organization_name": org,
            "full_name": f"{org} Admin",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    me = (await client.get(f"{API}/auth/me", headers=bearer(token))).json()
    return Account(token, email, me["user"]["id"], me["user"]["organization_id"])


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> Account:
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    me = (await client.get(f"{API}/auth/me", headers=bearer(token))).json()
    return Account(token, email, me["user"]["id"], me["user"]["organization_id"])


async def add_user(
    client: httpx.AsyncClient, admin: Account, email: str, role: str = "employee"
) -> Account:
    response = await client.post(
        f"{API}/users",
        headers=bearer(admin.token),
        json={"email": email, "full_name": email.split("@")[0], "password": PASSWORD, "role": role},
    )
    assert response.status_code == 201, response.text
    return await login(client, email)
