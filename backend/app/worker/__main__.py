"""Worker process entry point.

    python -m app.worker               # run until SIGTERM / Ctrl+C
    python -m app.worker --healthcheck # exit 0 if the worker's heartbeat is fresh

The heartbeat is a file touched every few seconds by a separate task, so the
health check reflects "the process and its event loop are alive", even while a
long job is running.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from pathlib import Path

import app.db.models  # noqa: F401  (registers all ORM models)
from app import __version__
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.llm.embeddings import build_embedder
from app.modules.documents.storage import LocalStorage
from app.worker.ingestion import Ingestor

logger = logging.getLogger("nexusai.worker")
HEARTBEAT_SECONDS = 5
HEARTBEAT_MAX_AGE = 60


async def _heartbeat(path: Path, stop: asyncio.Event) -> None:
    while not stop.is_set():
        path.touch()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), HEARTBEAT_SECONDS)


async def run(settings: Settings) -> None:
    db = Database(settings)
    embedder = build_embedder(settings)
    ingestor = Ingestor(db.session_factory, LocalStorage(settings.storage_dir), embedder, settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    heartbeat = asyncio.create_task(_heartbeat(Path(settings.worker_heartbeat_file), stop))
    logger.info(
        "worker started",
        extra={
            "worker_id": ingestor.worker_id,
            "version": __version__,
            "embedding_model": embedder.model_name,
        },
    )
    try:
        while not stop.is_set():
            try:
                busy = await ingestor.run_once()
            except Exception:  # e.g. database briefly unavailable: wait and try again
                logger.exception("worker loop error")
                busy = False
            if not busy:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
    finally:
        stop.set()
        await heartbeat
        if hasattr(embedder, "aclose"):
            await embedder.aclose()
        await db.dispose()
        logger.info("worker stopped", extra={"worker_id": ingestor.worker_id})


def healthcheck(settings: Settings) -> int:
    try:
        age = time.time() - os.path.getmtime(settings.worker_heartbeat_file)
    except OSError:
        return 1
    return 0 if age < HEARTBEAT_MAX_AGE else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--healthcheck", action="store_true", help="check the heartbeat and exit")
    args = parser.parse_args()
    settings = get_settings()
    if args.healthcheck:
        sys.exit(healthcheck(settings))
    configure_logging(settings.log_level, settings.log_json)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
