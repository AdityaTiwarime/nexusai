"""Agent runs, approval requests, and the records agents can create.

- AgentRun: one request ("create a ticket and email the customer"), its plan and
  the status of every step.
- ApprovalRequest: a frozen snapshot of the exact actions a human must approve
  before any of them runs. What is approved is exactly what executes.
- Ticket / OutboxMessage: what the tools created. With a ServiceNow or email
  connection (Phase 6) they also point to the record in the external system;
  without one they are internal records only.

Side effects carry (agent_run_id, step_index) with a unique constraint, so a step
can never create its record twice, even if execution is retried.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPrimaryKey


class RunStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


def _in(values: type[StrEnum]) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class AgentRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(RunStatus)})", name="status_valid"),
        Index("ix_agent_runs_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_agent_runs_requested_by", "requested_by"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    request: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    planner: Mapped[str] = mapped_column(String(100), nullable=False)
    # [{index, tool, args, reason, mutating, status, result, error}]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(ApprovalStatus)})", name="status_valid"),
        UniqueConstraint("agent_run_id", name="uq_approval_requests_agent_run_id"),
        Index("ix_approval_requests_organization_id_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Snapshot of the mutating steps: [{index, tool, args, reason}]
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(String(1000))


class Ticket(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="priority_valid"),
        UniqueConstraint("organization_id", "number", name="uq_tickets_organization_number"),
        UniqueConstraint("agent_run_id", "step_index", name="uq_tickets_run_step"),
        Index("ix_tickets_external_system_external_id", "external_system", "external_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    customer_email: Mapped[str | None] = mapped_column(String(320))
    # Set when the ticket was created in an external system (e.g. ServiceNow).
    external_system: Mapped[str | None] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(100))
    external_number: Mapped[str | None] = mapped_column(String(64))
    external_url: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    step_index: Mapped[int | None] = mapped_column(Integer)


class OutboxMessage(UUIDPrimaryKey, Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "step_index", name="uq_outbox_messages_run_step"),
        Index("ix_outbox_messages_organization_id_created_at", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # "recorded" = no email connection configured, nothing sent; "sent" = handed to the provider.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="recorded")
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    step_index: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
