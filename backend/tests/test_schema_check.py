"""The init-container check that holds API/worker pods until migrations are done."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema_check import (
    database_revision,
    migration_scripts,
    schema_status,
    wait_for_schema,
)
from tests.conftest import TEST_DATABASE_URL

HEAD = "0006"


@pytest.fixture
def scripts():
    return migration_scripts()


@pytest.mark.parametrize(
    ("revision", "expected"),
    [
        (HEAD, "ready"),
        (None, "pending"),  # nothing migrated yet
        ("0005", "pending"),  # the migration job hasn't finished
        ("0099_from_a_newer_release", "newer"),  # rollback onto a newer schema
    ],
)
def test_schema_status(scripts, revision, expected):
    assert schema_status(scripts, revision) == expected


async def _set_revision(value: str) -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": value})
    finally:
        await engine.dispose()


async def test_reads_the_migrated_test_database():
    assert await database_revision(TEST_DATABASE_URL) == HEAD
    assert await wait_for_schema(TEST_DATABASE_URL, max_wait=1) == "ready"


async def test_times_out_while_migrations_are_pending():
    await _set_revision("0005")
    try:
        assert await wait_for_schema(TEST_DATABASE_URL, max_wait=0.3, interval=0.1) == "pending"
    finally:
        await _set_revision(HEAD)


async def test_unreachable_database_counts_as_pending():
    url = "postgresql+asyncpg://nexus:nexus@127.0.0.1:1/nexusai"
    assert await wait_for_schema(url, max_wait=0.2, interval=0.1) == "pending"
