"""Document business rules: visibility, upload, versioning, reprocessing, deletion.

Visibility: a document has a `min_role`. A caller sees it only if their role ranks
at least that high (employee < manager < org_admin). Hidden documents behave like
missing ones (404), the same rule used for other tenants' data.

Upload is "store, record, enqueue" in one transaction. The heavy work (parsing,
chunking, embeddings) happens in the worker, so uploads return immediately.
"""

import hashlib
import logging
import uuid

from fastapi import Request
from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.auth.roles import ROLE_RANK, Role, rank
from app.modules.documents.models import (
    Document,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    JobStatus,
)
from app.modules.documents.parsing import validate_upload
from app.modules.documents.storage import Storage, document_prefix, version_key

logger = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class Duplicate(Exception):
    def __init__(self, document_id: uuid.UUID | None) -> None:
        super().__init__("This file has already been uploaded")
        # Only set when the caller may see the existing document.
        self.document_id = document_id


def visible_roles(user: User) -> list[str]:
    return [role for role, value in ROLE_RANK.items() if value <= rank(user.role)]


def visible_documents(user: User) -> Select[tuple[Document]]:
    return select(Document).where(
        Document.organization_id == user.organization_id,
        Document.min_role.in_(visible_roles(user)),
    )


async def get_visible(session: AsyncSession, document_id: uuid.UUID, user: User) -> Document:
    document = await session.scalar(visible_documents(user).where(Document.id == document_id))
    if document is None:
        raise NotFound
    return document


def can_manage(user: User, document: Document) -> bool:
    """Org admins manage every document; managers manage the ones they uploaded."""
    if user.role == Role.ORG_ADMIN:
        return True
    return user.role == Role.MANAGER and document.uploaded_by == user.id


def check_min_role(user: User, min_role: str) -> None:
    # Nobody can create a document they wouldn't be allowed to see themselves.
    if rank(min_role) > rank(user.role):
        raise Forbidden("You cannot restrict a document above your own role")


async def _find_duplicate(session: AsyncSession, user: User, sha256: str) -> Duplicate | None:
    existing = await session.scalar(
        select(DocumentVersion.document_id).where(
            DocumentVersion.organization_id == user.organization_id,
            DocumentVersion.sha256 == sha256,
        )
    )
    if existing is None:
        return None
    visible = await session.scalar(visible_documents(user).where(Document.id == existing))
    return Duplicate(existing if visible else None)


def _enqueue(session: AsyncSession, document: Document, version: DocumentVersion) -> None:
    session.add(
        IngestionJob(
            document_id=document.id,
            version_id=version.id,
            organization_id=document.organization_id,
            status=JobStatus.QUEUED,
        )
    )


async def _store_version(
    session: AsyncSession,
    storage: Storage,
    *,
    user: User,
    document: Document,
    version: int,
    filename: str,
    data: bytes,
    sha256: str,
) -> DocumentVersion:
    key = version_key(document.organization_id, document.id, version, document.file_type)
    row = DocumentVersion(
        id=uuid.uuid4(),
        document_id=document.id,
        organization_id=document.organization_id,
        version=version,
        original_filename=filename[:255],
        storage_key=key,
        size_bytes=len(data),
        sha256=sha256,
        uploaded_by=user.id,
    )
    session.add(row)
    storage.put(key, data)
    return row


async def _commit_or_cleanup(
    session: AsyncSession, storage: Storage, key: str, user: User, sha256: str
) -> None:
    try:
        await session.commit()
    except IntegrityError:
        # Two identical uploads raced; the unique (organization_id, sha256) caught it.
        await session.rollback()
        storage.delete_prefix(key)
        await session.refresh(user)  # rollback expired it
        duplicate = await _find_duplicate(session, user, sha256)
        raise duplicate or Duplicate(None) from None
    except BaseException:
        await session.rollback()
        storage.delete_prefix(key)
        raise


async def create_document(
    session: AsyncSession,
    storage: Storage,
    *,
    user: User,
    filename: str,
    data: bytes,
    title: str | None,
    min_role: str,
    max_bytes: int,
    request: Request | None,
) -> Document:
    file_type = validate_upload(filename, data, max_bytes)
    check_min_role(user, min_role)
    sha256 = hashlib.sha256(data).hexdigest()
    if duplicate := await _find_duplicate(session, user, sha256):
        raise duplicate

    document = Document(
        id=uuid.uuid4(),
        organization_id=user.organization_id,
        uploaded_by=user.id,
        title=(title or "").strip() or filename.rsplit(".", 1)[0][:200] or "Untitled",
        file_type=file_type,
        min_role=min_role,
        status=DocumentStatus.QUEUED,
        current_version=1,
        doc_metadata={},
    )
    session.add(document)
    await session.flush()
    version = await _store_version(
        session, storage, user=user, document=document, version=1,
        filename=filename, data=data, sha256=sha256,
    )  # fmt: skip
    await session.flush()
    _enqueue(session, document, version)
    audit.record(
        session,
        action="document.uploaded",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=request,
        target_type="document",
        target_id=document.id,
        details={"title": document.title, "file_type": file_type, "size_bytes": len(data)},
    )
    await _commit_or_cleanup(
        session, storage, document_prefix(document.organization_id, document.id), user, sha256
    )
    await session.refresh(document)
    return document


async def _supersede_pending_jobs(session: AsyncSession, document_id: uuid.UUID) -> None:
    await session.execute(
        update(IngestionJob)
        .where(IngestionJob.document_id == document_id, IngestionJob.status == JobStatus.QUEUED)
        .values(status=JobStatus.SUPERSEDED)
    )


async def add_version(
    session: AsyncSession,
    storage: Storage,
    *,
    user: User,
    document: Document,
    filename: str,
    data: bytes,
    max_bytes: int,
    request: Request | None,
) -> Document:
    if not can_manage(user, document):
        raise Forbidden("Only an org admin or the uploader can change this document")
    file_type = validate_upload(filename, data, max_bytes)
    if file_type != document.file_type:
        raise Forbidden(f"A new version must also be a .{document.file_type} file")
    sha256 = hashlib.sha256(data).hexdigest()
    if duplicate := await _find_duplicate(session, user, sha256):
        raise duplicate

    new_version = document.current_version + 1
    await _supersede_pending_jobs(session, document.id)
    version = await _store_version(
        session, storage, user=user, document=document, version=new_version,
        filename=filename, data=data, sha256=sha256,
    )  # fmt: skip
    document.current_version = new_version
    document.status = DocumentStatus.QUEUED
    document.error = None
    await session.flush()
    _enqueue(session, document, version)
    audit.record(
        session,
        action="document.version_added",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=request,
        target_type="document",
        target_id=document.id,
        details={"version": new_version, "size_bytes": len(data)},
    )
    await _commit_or_cleanup(session, storage, version.storage_key, user, sha256)
    await session.refresh(document)
    return document


async def reprocess(
    session: AsyncSession, *, user: User, document: Document, request: Request | None
) -> Document:
    if not can_manage(user, document):
        raise Forbidden("Only an org admin or the uploader can reprocess this document")
    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version == document.current_version,
        )
    )
    if version is None:  # pragma: no cover - guarded by the schema
        raise NotFound
    await _supersede_pending_jobs(session, document.id)
    document.status = DocumentStatus.QUEUED
    document.error = None
    _enqueue(session, document, version)
    audit.record(
        session,
        action="document.reprocess_requested",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=request,
        target_type="document",
        target_id=document.id,
        details={"version": document.current_version},
    )
    await session.commit()
    await session.refresh(document)
    return document


async def update_document(
    session: AsyncSession,
    *,
    user: User,
    document: Document,
    title: str | None,
    min_role: str | None,
    request: Request | None,
) -> Document:
    if not can_manage(user, document):
        raise Forbidden("Only an org admin or the uploader can change this document")
    changes: dict[str, dict[str, str]] = {}
    if title is not None and title.strip() and title.strip() != document.title:
        changes["title"] = {"from": document.title, "to": title.strip()}
        document.title = title.strip()
    if min_role is not None and min_role != document.min_role:
        check_min_role(user, min_role)
        changes["min_role"] = {"from": document.min_role, "to": min_role}
        document.min_role = min_role
    if changes:
        audit.record(
            session,
            action="document.updated",
            organization_id=user.organization_id,
            actor_id=user.id,
            request=request,
            target_type="document",
            target_id=document.id,
            details=changes,
        )
        await session.commit()
        await session.refresh(document)
    return document


async def delete_document(
    session: AsyncSession,
    storage: Storage,
    *,
    user: User,
    document: Document,
    request: Request | None,
) -> None:
    if not can_manage(user, document):
        raise Forbidden("Only an org admin or the uploader can delete this document")
    prefix = document_prefix(document.organization_id, document.id)
    audit.record(
        session,
        action="document.deleted",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=request,
        target_type="document",
        target_id=document.id,
        details={"title": document.title},
    )
    await session.delete(document)  # versions, chunks and jobs cascade in the database
    await session.commit()
    # Files go after the commit: a failed commit must not leave a row without its file.
    # A crash here leaves orphan files at worst, never a broken document.
    storage.delete_prefix(prefix)
