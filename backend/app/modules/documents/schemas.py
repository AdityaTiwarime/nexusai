import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.modules.auth.roles import Role


class DocumentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    original_filename: str
    size_bytes: int
    sha256: str
    uploaded_by: uuid.UUID | None
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    file_type: str
    min_role: str
    status: str
    current_version: int
    chunk_count: int
    page_count: int | None
    error: str | None
    # The ORM attribute is `doc_metadata` ("metadata" is reserved by SQLAlchemy).
    metadata: dict[str, Any] = Field(validation_alias=AliasChoices("doc_metadata", "metadata"))
    uploaded_by: uuid.UUID | None
    uploaded_by_email: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class DocumentDetail(DocumentOut):
    versions: list[DocumentVersionOut]


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int
    status_counts: dict[str, int]


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    min_role: Role | None = None


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_index: int
    content: str
    page_start: int | None
    page_end: int | None
    char_count: int


class ChunkPage(BaseModel):
    items: list[ChunkOut]
    total: int
    limit: int
    offset: int
