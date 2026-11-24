"""Migrations must apply and roll back cleanly against a real PostgreSQL database."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL, run_alembic

HEAD = "0006"


async def _current_revision() -> str | None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
            if not exists:
                return None
            return await conn.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()


async def _table_exists(name: str) -> bool:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            return bool(await conn.scalar(text(f"SELECT to_regclass('{name}') IS NOT NULL")))
    finally:
        await engine.dispose()


async def test_upgrade_to_head_and_downgrade_to_base():
    up = run_alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    assert await _current_revision() == HEAD
    assert await _table_exists("users")
    assert await _table_exists("document_chunks")

    down = run_alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr
    assert await _current_revision() is None
    assert not await _table_exists("users")

    # Leave the database migrated for every test that runs afterwards.
    assert run_alembic("upgrade", "head").returncode == 0


def test_single_migration_head():
    result = run_alembic("heads")
    assert result.returncode == 0, result.stderr
    assert len([line for line in result.stdout.splitlines() if line.strip()]) == 1


def test_models_match_migrations():
    """`alembic check` fails if the ORM models have changes no migration covers."""
    result = run_alembic("check")
    assert result.returncode == 0, result.stdout + result.stderr
