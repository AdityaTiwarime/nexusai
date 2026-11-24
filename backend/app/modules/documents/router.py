"""Document endpoints.

- Any organization member can list, view, read chunks of and download the
  documents their role is allowed to see.
- Managers and org admins can upload. Org admins manage every document;
  managers manage the documents they uploaded.
"""

import uuid
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import status as http
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings_dep
from app.core.config import Settings
from app.modules.auth.deps import require_org_member, require_roles
from app.modules.auth.models import User
from app.modules.auth.roles import Role
from app.modules.documents import service
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion
from app.modules.documents.parsing import UploadRejected
from app.modules.documents.schemas import (
    ChunkOut,
    ChunkPage,
    DocumentDetail,
    DocumentOut,
    DocumentPage,
    DocumentUpdate,
    DocumentVersionOut,
)
from app.modules.documents.storage import Storage

router = APIRouter(prefix="/documents", tags=["documents"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
Member = Annotated[User, Depends(require_org_member())]
Uploader = Annotated[User, Depends(require_roles(Role.ORG_ADMIN, Role.MANAGER))]
DocRole = Literal["employee", "manager", "org_admin"]


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


StorageDep = Annotated[Storage, Depends(get_storage)]


async def _read_upload(file: UploadFile, settings: Settings) -> tuple[str, bytes]:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    # Read one byte past the limit: enough to know it's too big without reading it all.
    data = await file.read(max_bytes + 1)
    return (file.filename or "upload"), data


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, UploadRejected):
        return HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, service.Duplicate):
        detail: dict[str, object] = {"message": str(exc)}
        if exc.document_id:
            detail["document_id"] = str(exc.document_id)
        return HTTPException(http.HTTP_409_CONFLICT, detail)
    if isinstance(exc, service.Forbidden):
        return HTTPException(http.HTTP_403_FORBIDDEN, str(exc) or "Not allowed")
    return HTTPException(http.HTTP_404_NOT_FOUND, "Document not found")


async def _visible(session: AsyncSession, document_id: uuid.UUID, user: User) -> Document:
    try:
        return await service.get_visible(session, document_id, user)
    except service.NotFound as exc:
        raise _errors(exc) from None


async def _with_uploader(session: AsyncSession, document: Document) -> DocumentOut:
    out = DocumentOut.model_validate(document)
    if document.uploaded_by:
        out.uploaded_by_email = await session.scalar(
            select(User.email).where(User.id == document.uploaded_by)
        )
    return out


async def _detail(session: AsyncSession, document: Document) -> DocumentDetail:
    versions = await session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version.desc())
    )
    base = await _with_uploader(session, document)
    return DocumentDetail(
        **base.model_dump(),
        versions=[DocumentVersionOut.model_validate(v) for v in versions],
    )


@router.post("", response_model=DocumentDetail, status_code=http.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    caller: Uploader,
    session: Session,
    settings: SettingsDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File(description="PDF, DOCX, TXT, CSV or MD")],
    title: Annotated[str | None, Form(max_length=200)] = None,
    min_role: Annotated[DocRole, Form()] = "employee",
) -> DocumentDetail:
    filename, data = await _read_upload(file, settings)
    try:
        document = await service.create_document(
            session,
            storage,
            user=caller,
            filename=filename,
            data=data,
            title=title,
            min_role=min_role,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            request=request,
        )
    except (UploadRejected, service.Duplicate, service.Forbidden) as exc:
        raise _errors(exc) from None
    return await _detail(session, document)


@router.get("", response_model=DocumentPage)
async def list_documents(
    caller: Member,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[Literal["queued", "processing", "ready", "failed"] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> DocumentPage:
    base = service.visible_documents(caller)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = base.where(Document.title.ilike(f"%{escaped}%", escape="\\"))

    # Counts per status for the visible (and title-filtered) set, for the UI's filter tabs.
    visible = base.subquery()
    counts_rows = await session.execute(
        select(visible.c.status, func.count()).group_by(visible.c.status)
    )
    counts = {row_status: count for row_status, count in counts_rows.all()}
    filtered = base.where(Document.status == status) if status else base
    total = await session.scalar(select(func.count()).select_from(filtered.subquery()))
    rows = await session.execute(
        filtered.add_columns(User.email)
        .outerjoin(User, User.id == Document.uploaded_by)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(limit)
        .offset(offset)
    )
    items = []
    for document, email in rows.all():
        out = DocumentOut.model_validate(document)
        out.uploaded_by_email = email
        items.append(out)
    return DocumentPage(
        items=items, total=total or 0, limit=limit, offset=offset, status_counts=counts
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, caller: Member, session: Session) -> DocumentDetail:
    return await _detail(session, await _visible(session, document_id, caller))


@router.get("/{document_id}/chunks", response_model=ChunkPage)
async def list_chunks(
    document_id: uuid.UUID,
    caller: Member,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChunkPage:
    document = await _visible(session, document_id, caller)
    where = (
        DocumentChunk.document_id == document.id,
        DocumentChunk.organization_id == caller.organization_id,
    )
    total = await session.scalar(select(func.count()).select_from(DocumentChunk).where(*where))
    chunks = await session.scalars(
        select(DocumentChunk)
        .where(*where)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
        .offset(offset)
    )
    return ChunkPage(
        items=[ChunkOut.model_validate(c) for c in chunks],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}/download")
async def download_document(
    document_id: uuid.UUID,
    caller: Member,
    session: Session,
    storage: StorageDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    document = await _visible(session, document_id, caller)
    row = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version == (version or document.current_version),
        )
    )
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Version not found")
    return Response(
        content=storage.get(row.storage_key),
        # Always a download, never rendered inline: uploaded files are untrusted content.
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.original_filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{document_id}/versions", response_model=DocumentDetail)
async def upload_version(
    document_id: uuid.UUID,
    request: Request,
    caller: Uploader,
    session: Session,
    settings: SettingsDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> DocumentDetail:
    document = await _visible(session, document_id, caller)
    filename, data = await _read_upload(file, settings)
    try:
        document = await service.add_version(
            session,
            storage,
            user=caller,
            document=document,
            filename=filename,
            data=data,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            request=request,
        )
    except (UploadRejected, service.Duplicate, service.Forbidden) as exc:
        raise _errors(exc) from None
    return await _detail(session, document)


@router.post("/{document_id}/reprocess", response_model=DocumentDetail)
async def reprocess_document(
    document_id: uuid.UUID, request: Request, caller: Uploader, session: Session
) -> DocumentDetail:
    document = await _visible(session, document_id, caller)
    try:
        document = await service.reprocess(session, user=caller, document=document, request=request)
    except service.Forbidden as exc:
        raise _errors(exc) from None
    return await _detail(session, document)


@router.patch("/{document_id}", response_model=DocumentDetail)
async def update_document(
    document_id: uuid.UUID,
    body: DocumentUpdate,
    request: Request,
    caller: Uploader,
    session: Session,
) -> DocumentDetail:
    if body.min_role == Role.SUPER_ADMIN:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid role for a document")
    document = await _visible(session, document_id, caller)
    try:
        document = await service.update_document(
            session,
            user=caller,
            document=document,
            title=body.title,
            min_role=body.min_role.value if body.min_role else None,
            request=request,
        )
    except service.Forbidden as exc:
        raise _errors(exc) from None
    return await _detail(session, document)


@router.delete("/{document_id}", status_code=http.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    request: Request,
    caller: Uploader,
    session: Session,
    storage: StorageDep,
) -> Response:
    document = await _visible(session, document_id, caller)
    try:
        await service.delete_document(
            session, storage, user=caller, document=document, request=request
        )
    except service.Forbidden as exc:
        raise _errors(exc) from None
    return Response(status_code=http.HTTP_204_NO_CONTENT)
