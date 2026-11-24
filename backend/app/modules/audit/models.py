"""Append-only audit log of security-relevant actions."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKey


class AuditLog(UUIDPrimaryKey, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # Every audit query is "this organization's events, newest first".
        Index("ix_audit_logs_organization_id_created_at", "organization_id", "created_at"),
    )

    # Nullable: some events (e.g. a failed login for an unknown email) have no tenant.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
