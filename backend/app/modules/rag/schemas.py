import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=20)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class PassageOut(BaseModel):
    document_id: uuid.UUID
    document_title: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    content: str
    score: float
    similarity: float
    keyword_match: bool


class SearchResponse(BaseModel):
    query: str
    results: list[PassageOut]
    candidates: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class Citation(BaseModel):
    number: int
    available: bool = True  # False once the source is deleted or no longer visible to you
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    chunk_index: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    snippet: str | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    status: str | None
    citations: list[Citation]
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    created_at: datetime


class AskResponse(BaseModel):
    conversation_id: uuid.UUID
    question: MessageOut
    answer: MessageOut


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
