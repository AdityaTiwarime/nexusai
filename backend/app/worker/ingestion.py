"""Ingestion jobs, with PostgreSQL as the queue.

Why PostgreSQL and not Redis for this queue: the job row is written in the same
transaction as the upload, so a document can never exist without its job (or the
other way round), and a Redis outage can't lose work. `FOR UPDATE SKIP LOCKED`
lets several workers pull jobs concurrently without ever taking the same one.

Lifecycle of a job:
    queued -> running -> done
                      -> queued again (retryable error; exponential backoff)
                      -> failed (bad file, or out of attempts)
                      -> superseded (a newer version was uploaded meanwhile)

A worker that crashes mid-job leaves it "running" with an old `locked_at`; after
`job_lock_timeout_seconds` another worker reclaims it. Processing is idempotent
(old chunks are replaced in one transaction), so running a job twice is safe.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.llm.embeddings import EmbeddingError, EmbeddingProvider
from app.modules.documents.chunking import chunk_segments
from app.modules.documents.models import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    JobStatus,
)
from app.modules.documents.parsing import ParseError, parse_document
from app.modules.documents.storage import Storage

logger = logging.getLogger("nexusai.worker")

_CLAIM = text(
    """
    UPDATE ingestion_jobs
       SET status = 'running', attempts = attempts + 1, locked_at = now(), worker_id = :worker
     WHERE id = (
           SELECT id FROM ingestion_jobs
            WHERE (status = 'queued' AND run_after <= now())
               OR (status = 'running' AND locked_at < now() - make_interval(secs => :lock_timeout))
            ORDER BY run_after
            FOR UPDATE SKIP LOCKED
            LIMIT 1)
    RETURNING id, document_id, version_id, attempts
    """
)


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    attempts: int


class _Superseded(Exception):
    pass


class _Permanent(Exception):
    """Fails the document with a message that is safe to show to users."""


class Ingestor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: Storage,
        embedder: EmbeddingProvider,
        settings: Settings,
        worker_id: str | None = None,
    ) -> None:
        self.sessions = session_factory
        self.storage = storage
        self.embedder = embedder
        self.settings = settings
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"

    async def claim(self) -> ClaimedJob | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    _CLAIM,
                    {
                        "worker": self.worker_id,
                        "lock_timeout": self.settings.job_lock_timeout_seconds,
                    },
                )
            ).first()
            await session.commit()
        return ClaimedJob(*row) if row else None

    async def run_once(self) -> bool:
        """Claim and process one job. Returns False when the queue is empty."""
        job = await self.claim()
        if job is None:
            return False
        await self.handle(job)
        return True

    async def handle(self, job: ClaimedJob) -> None:
        started = time.perf_counter()
        log = {"job_id": str(job.id), "document_id": str(job.document_id), "attempt": job.attempts}
        try:
            chunks = await self._process(job)
        except _Superseded:
            await self._finish(job, JobStatus.SUPERSEDED)
            logger.info("job superseded", extra=log)
        except _Permanent as exc:
            await self._fail(job, str(exc), str(exc), retryable=False)
            logger.warning("job failed", extra={**log, "error": str(exc)})
        except EmbeddingError as exc:
            await self._fail(job, str(exc), "Embedding failed", retryable=exc.retryable)
            logger.warning("embedding error", extra={**log, "error": str(exc)})
        except Exception as exc:
            logger.exception("unexpected ingestion error", extra=log)
            await self._fail(
                job, f"{type(exc).__name__}: {exc}"[:1000], "Unexpected processing error",
                retryable=True,
            )  # fmt: skip
        else:
            logger.info(
                "job done",
                extra={
                    **log,
                    "chunks": chunks,
                    "ms": round((time.perf_counter() - started) * 1000),
                },
            )

    async def _load(
        self, session: AsyncSession, job: ClaimedJob, lock: bool = False
    ) -> tuple[Document, DocumentVersion]:
        query = select(Document).where(Document.id == job.document_id)
        document = await session.scalar(query.with_for_update() if lock else query)
        version = await session.get(DocumentVersion, job.version_id)
        if document is None or version is None or version.version != document.current_version:
            raise _Superseded
        return document, version

    async def _process(self, job: ClaimedJob) -> int:
        if job.attempts > self.settings.job_max_attempts:
            # Reclaimed from crashed workers too many times: likely a file that kills the worker.
            raise _Permanent("Processing kept failing; the file may be too large or malformed")

        async with self.sessions() as session:
            document, version = await self._load(session, job)
            document.status = DocumentStatus.PROCESSING
            document.error = None
            file_type, storage_key = document.file_type, version.storage_key
            await session.commit()

        try:
            data = self.storage.get(storage_key)
        except FileNotFoundError:
            raise _Permanent("The uploaded file is missing from storage") from None

        try:
            # Parsing is CPU-bound; keep the event loop (and heartbeat) responsive.
            parsed = await asyncio.to_thread(parse_document, file_type, data)
        except ParseError as exc:
            raise _Permanent(str(exc)) from None

        chunks = chunk_segments(
            parsed.segments, self.settings.chunk_size, self.settings.chunk_overlap
        )
        vectors: list[list[float]] = []
        size = self.settings.embedding_batch_size
        for start in range(0, len(chunks), size):
            vectors.extend(
                await self.embedder.embed([c.text for c in chunks[start : start + size]])
            )

        async with self.sessions() as session:
            # Lock the document row: a concurrent job for the same document waits here,
            # and a version uploaded while we were embedding is detected.
            document, version = await self._load(session, job, lock=True)
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            if chunks:
                await session.execute(
                    insert(DocumentChunk),
                    [
                        {
                            "id": uuid.uuid4(),
                            "document_id": document.id,
                            "version_id": version.id,
                            "organization_id": document.organization_id,
                            "chunk_index": chunk.index,
                            "content": chunk.text,
                            "page_start": chunk.page_start,
                            "page_end": chunk.page_end,
                            "char_count": len(chunk.text),
                            "embedding": vector,
                            "embedding_model": self.embedder.model_name,
                        }
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ],
                )
            document.status = DocumentStatus.READY
            document.chunk_count = len(chunks)
            document.page_count = parsed.page_count
            document.doc_metadata = {**parsed.metadata, "characters": parsed.text_length}
            document.processed_at = datetime.now(UTC)
            document.error = None
            job_row = await session.get(IngestionJob, job.id)
            if job_row is not None:
                job_row.status = JobStatus.DONE
                job_row.finished_at = datetime.now(UTC)
                job_row.last_error = None
            await session.commit()
        return len(chunks)

    async def _finish(self, job: ClaimedJob, status: JobStatus) -> None:
        async with self.sessions() as session:
            row = await session.get(IngestionJob, job.id)
            if row is not None:
                row.status = status
                row.finished_at = datetime.now(UTC)
                await session.commit()

    async def _fail(
        self, job: ClaimedJob, internal_error: str, user_error: str, *, retryable: bool
    ) -> None:
        async with self.sessions() as session:
            row = await session.get(IngestionJob, job.id)
            if row is None:  # document deleted meanwhile (jobs cascade)
                return
            row.last_error = internal_error[:1000]
            retry = retryable and row.attempts < self.settings.job_max_attempts
            if retry:
                delay = self.settings.job_retry_base_seconds * (2 ** (row.attempts - 1))
                row.status = JobStatus.QUEUED
                row.run_after = datetime.now(UTC) + timedelta(seconds=delay)
                row.locked_at = None
                row.worker_id = None
            else:
                row.status = JobStatus.FAILED
                row.finished_at = datetime.now(UTC)

            document = await session.get(Document, job.document_id)
            version = await session.get(DocumentVersion, job.version_id)
            if document and version and version.version == document.current_version:
                if retry:
                    document.status = DocumentStatus.QUEUED
                else:
                    document.status = DocumentStatus.FAILED
                    document.error = user_error[:500]
            await session.commit()
