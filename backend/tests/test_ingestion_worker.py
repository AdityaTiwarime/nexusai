"""Worker reliability: retries, permanent failures, crash recovery, concurrency, superseding."""

import asyncio
from pathlib import Path

from sqlalchemy import select, text

from app.db.session import Database
from app.llm.embeddings import EmbeddingError, HashingEmbedder
from app.modules.documents.models import IngestionJob
from app.modules.documents.storage import LocalStorage
from app.worker.__main__ import healthcheck
from app.worker.ingestion import Ingestor
from tests.conftest import API, bearer, drain, make_settings, register_org, upload
from tests.factories import make_blank_pdf


class FlakyEmbedder(HashingEmbedder):
    """Fails the first `failures` calls, then works."""

    def __init__(self, failures: int, retryable: bool = True) -> None:
        super().__init__()
        self.failures = failures
        self.retryable = retryable
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        if self.calls <= self.failures:
            raise EmbeddingError("provider unavailable", retryable=self.retryable)
        return await super().embed(texts)


def _worker(settings, embedder, worker_id="w") -> tuple[Ingestor, Database]:
    db = Database(settings)
    return Ingestor(
        db.session_factory, LocalStorage(settings.storage_dir), embedder, settings, worker_id
    ), db


async def _document(client, account, doc_id):
    return (await client.get(f"{API}/documents/{doc_id}", headers=bearer(account.token))).json()


async def _jobs(ingestor):
    async with ingestor.sessions() as session:
        return list(await session.scalars(select(IngestionJob).order_by(IngestionJob.created_at)))


async def test_retryable_errors_are_retried_with_backoff(client, settings):
    admin = await register_org(client)
    doc = (await upload(client, admin)).json()
    worker, db = _worker(
        settings.model_copy(update={"job_retry_base_seconds": 0}), FlakyEmbedder(2)
    )
    try:
        assert await worker.run_once()
        job = (await _jobs(worker))[0]
        assert job.status == "queued" and job.attempts == 1
        assert job.last_error == "provider unavailable"
        assert (await _document(client, admin, doc["id"]))["status"] == "queued"

        await drain(worker)
        job = (await _jobs(worker))[0]
        assert job.status == "done" and job.attempts == 3
        assert (await _document(client, admin, doc["id"]))["status"] == "ready"
    finally:
        await db.dispose()


async def test_backoff_delays_the_next_attempt(client, settings):
    admin = await register_org(client)
    await upload(client, admin)
    worker, db = _worker(
        settings.model_copy(update={"job_retry_base_seconds": 60}), FlakyEmbedder(1)
    )
    try:
        assert await worker.run_once()
        # The job is waiting for its backoff, so nothing is runnable now.
        assert not await worker.run_once()
        job = (await _jobs(worker))[0]
        async with worker.sessions() as session:
            delay = await session.scalar(
                text(
                    "SELECT extract(epoch FROM run_after - now())"
                    " FROM ingestion_jobs WHERE id = :id"
                ),
                {"id": job.id},
            )
        assert 50 < delay <= 60
    finally:
        await db.dispose()


async def test_document_fails_after_max_attempts(client, settings):
    admin = await register_org(client)
    doc = (await upload(client, admin)).json()
    worker, db = _worker(
        settings.model_copy(update={"job_retry_base_seconds": 0, "job_max_attempts": 2}),
        FlakyEmbedder(99),
    )
    try:
        assert await drain(worker) == 2
        assert (await _jobs(worker))[0].status == "failed"
        failed = await _document(client, admin, doc["id"])
        assert failed["status"] == "failed"
        assert failed["error"] == "Embedding failed"
    finally:
        await db.dispose()


async def test_non_retryable_errors_fail_immediately(client, settings):
    admin = await register_org(client)
    await upload(client, admin)
    worker, db = _worker(settings, FlakyEmbedder(1, retryable=False))
    try:
        assert await drain(worker) == 1
        job = (await _jobs(worker))[0]
        assert job.status == "failed" and job.attempts == 1
    finally:
        await db.dispose()


async def test_unparseable_file_fails_with_reason_and_can_be_reprocessed(client, ingestor):
    admin = await register_org(client)
    doc = (await upload(client, admin, "scan.pdf", make_blank_pdf())).json()
    assert await drain(ingestor) == 1  # no retries for a file that can't be parsed
    failed = await _document(client, admin, doc["id"])
    assert failed["status"] == "failed"
    assert "OCR" in failed["error"]

    response = await client.post(
        f"{API}/documents/{doc['id']}/reprocess", headers=bearer(admin.token)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued" and response.json()["error"] is None
    assert len(await _jobs(ingestor)) == 2


async def test_missing_file_fails_permanently(client, ingestor, settings):
    admin = await register_org(client)
    doc = (await upload(client, admin)).json()
    for path in Path(settings.storage_dir).rglob("original.txt"):
        path.unlink()
    await drain(ingestor)
    assert "missing" in (await _document(client, admin, doc["id"]))["error"]


async def test_crashed_worker_job_is_reclaimed(client, settings):
    admin = await register_org(client)
    doc = (await upload(client, admin)).json()
    crashed, db1 = _worker(settings, HashingEmbedder(), "crashed")
    rescuer, db2 = _worker(settings, HashingEmbedder(), "rescuer")
    try:
        job = await crashed.claim()  # claims the job, then "dies" without processing it
        assert job is not None
        assert not await rescuer.run_once()  # lock is still fresh: not taken

        async with rescuer.sessions() as session:
            await session.execute(
                text("UPDATE ingestion_jobs SET locked_at = now() - interval '1 hour'")
            )
            await session.commit()
        assert await rescuer.run_once()
        row = (await _jobs(rescuer))[0]
        assert row.status == "done" and row.worker_id == "rescuer" and row.attempts == 2
        assert (await _document(client, admin, doc["id"]))["status"] == "ready"
    finally:
        await db1.dispose()
        await db2.dispose()


async def test_job_that_keeps_crashing_workers_is_given_up(client, settings):
    admin = await register_org(client)
    doc = (await upload(client, admin)).json()
    worker, db = _worker(settings.model_copy(update={"job_max_attempts": 2}), HashingEmbedder())
    try:
        for _ in range(2):  # two workers claim it and crash
            assert await worker.claim() is not None
            async with worker.sessions() as session:
                await session.execute(
                    text("UPDATE ingestion_jobs SET locked_at = now() - interval '1 hour'")
                )
                await session.commit()
        assert await worker.run_once()  # third claim: attempts > max, give up
        assert (await _document(client, admin, doc["id"]))["status"] == "failed"
    finally:
        await db.dispose()


async def test_concurrent_workers_never_take_the_same_job(client, settings):
    admin = await register_org(client)
    for i in range(6):
        assert (
            await upload(client, admin, f"doc{i}.txt", f"Document number {i}".encode())
        ).status_code == 201
    workers = [_worker(settings, HashingEmbedder(), f"w{i}") for i in range(3)]
    try:
        claims = await asyncio.gather(*(w.claim() for w, _ in workers for _ in range(3)))
        ids = [c.id for c in claims if c is not None]
        assert len(ids) == 6
        assert len(set(ids)) == 6
    finally:
        for _, db in workers:
            await db.dispose()


async def test_job_for_an_outdated_version_is_superseded(client, ingestor):
    admin = await register_org(client)
    doc = (await upload(client, admin, "p.txt", b"Version one text")).json()
    in_flight = await ingestor.claim()  # v1 is being processed...

    await client.post(  # ...when v2 is uploaded
        f"{API}/documents/{doc['id']}/versions",
        headers=bearer(admin.token),
        files={"file": ("p.txt", b"Version two text", "text/plain")},
    )
    await ingestor.handle(in_flight)
    jobs = await _jobs(ingestor)
    assert jobs[0].status == "superseded"
    assert (await _document(client, admin, doc["id"]))["status"] == "queued"

    await drain(ingestor)
    chunks = (
        await client.get(f"{API}/documents/{doc['id']}/chunks", headers=bearer(admin.token))
    ).json()["items"]
    assert [c["content"] for c in chunks] == ["Version two text"]


async def test_queued_jobs_are_superseded_by_a_new_version(client, ingestor):
    admin = await register_org(client)
    doc = (await upload(client, admin, "p.txt", b"Version one")).json()
    await client.post(
        f"{API}/documents/{doc['id']}/versions",
        headers=bearer(admin.token),
        files={"file": ("p.txt", b"Version two", "text/plain")},
    )
    assert [j.status for j in await _jobs(ingestor)] == ["superseded", "queued"]
    assert await drain(ingestor) == 1


async def test_reprocessing_is_idempotent(client, ingestor):
    admin = await register_org(client)
    doc = (await upload(client, admin, "p.md", b"# Title\n\nSome content\n\nMore content")).json()
    await drain(ingestor)
    first = (await _document(client, admin, doc["id"]))["chunk_count"]
    await client.post(f"{API}/documents/{doc['id']}/reprocess", headers=bearer(admin.token))
    await drain(ingestor)
    again = await _document(client, admin, doc["id"])
    assert again["status"] == "ready" and again["chunk_count"] == first


def test_worker_healthcheck_reads_the_heartbeat(tmp_path):
    settings = make_settings(worker_heartbeat_file=str(tmp_path / "beat"))
    assert healthcheck(settings) == 1
    (tmp_path / "beat").touch()
    assert healthcheck(settings) == 0
