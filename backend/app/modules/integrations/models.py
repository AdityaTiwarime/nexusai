"""Connections to external systems, their call log and received webhooks.

Secrets (passwords, client secrets, API keys, webhook signing secrets) are stored
only encrypted (see app/core/crypto.py) and are never returned by the API.
"""

import uuid
from datetime import datetime
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

KINDS = ("servicenow", "salesforce", "email")


class Integration(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "integrations"
    __table_args__ = (
        CheckConstraint("kind IN ('servicenow', 'salesforce', 'email')", name="kind_valid"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("health_status IN ('unknown', 'ok', 'error')", name="health_status_valid"),
        # One connection per system per organization: tools know which one to use.
        UniqueConstraint("organization_id", "kind", name="uq_integrations_organization_kind"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    secrets_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class IntegrationCall(UUIDPrimaryKey, Base):
    """One logical call to an external system (including its retries)."""

    __tablename__ = "integration_calls"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure')", name="outcome_valid"),
        Index("ix_integration_calls_integration_id_created_at", "integration_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WebhookEvent(UUIDPrimaryKey, Base):
    """Every accepted webhook. The unique event id makes redelivery harmless."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("integration_id", "event_id", name="uq_webhook_events_integration_event"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
