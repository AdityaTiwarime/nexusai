"""Wait until the database schema is ready for this release.

Kubernetes runs this in an init container of the API and worker pods, so they only
start once the migration job has finished:

    python -m app.db.schema_check [--timeout 300]

The schema is accepted when it is at this release's head revision, or at a revision
this release doesn't know. The second case is a rollback: a newer release already
migrated the database, and because migrations are kept backward compatible (add
first, remove a release later) the older code can run on the newer schema.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from app.core.config import get_settings

logger = logging.getLogger("nexusai.schema_check")

Status = Literal["ready", "pending", "newer"]
BACKEND_DIR = Path(__file__).resolve().parents[2]


def migration_scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def schema_status(scripts: ScriptDirectory, db_revision: str | None) -> Status:
    if db_revision == scripts.get_current_head():
        return "ready"
    if db_revision is None:
        return "pending"
    try:
        scripts.get_revision(db_revision)
    except CommandError:
        return "newer"  # written by a later release
    return "pending"  # an older revision this release still has to migrate from


async def database_revision(database_url: str) -> str | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            if not await conn.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL")):
                return None
            return await conn.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()


async def wait_for_schema(database_url: str, max_wait: float, interval: float = 2.0) -> Status:
    scripts = migration_scripts()
    deadline = time.monotonic() + max_wait
    while True:
        try:
            status = schema_status(scripts, await database_revision(database_url))
        except OSError as exc:  # database not reachable yet
            logger.info("database not reachable yet: %s", exc)
            status = "pending"
        if status != "pending":
            return status
        if time.monotonic() >= deadline:
            return status
        await asyncio.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = str(get_settings().database_url)
    status = asyncio.run(wait_for_schema(url, args.timeout))
    if status == "pending":
        logger.error(
            "schema is not migrated after %.0fs (is the migration job running?)", args.timeout
        )
        return 1
    logger.info(
        "schema ready (%s)", "head" if status == "ready" else "newer release, backward compatible"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
