"""Migrations must apply and roll back cleanly against a real PostgreSQL database."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NEXUS_DATABASE_URL": TEST_DATABASE_URL}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


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


async def test_upgrade_to_head_and_downgrade_to_base():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    assert await _current_revision() == "0001"

    down = _alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr
    assert await _current_revision() is None

    # Leave the database migrated for anything that runs afterwards.
    assert _alembic("upgrade", "head").returncode == 0


def test_single_migration_head():
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert len([line for line in result.stdout.splitlines() if line.strip()]) == 1
