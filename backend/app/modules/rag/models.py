"""Conversations and messages for the AI chat.

A conversation belongs to one user: other users (even admins of the same
organization) can't read it. Assistant messages keep the citations they were
answered from, plus the model, token usage and latency, so answers can be
audited and costs measured later (Phase 8).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPrimaryKey


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    NO_EVIDENCE = "no_evidence"  # the documents don't support an answer


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class Message(UUIDPrimaryKey, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        CheckConstraint(
            "status IS NULL OR status IN ('answered', 'no_evidence')", name="status_valid"
        ),
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Assistant messages only.
    status: Mapped[str | None] = mapped_column(String(16))
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
