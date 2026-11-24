"""Organizations (tenants), users and refresh tokens."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPrimaryKey
from app.modules.auth.roles import Role

_ROLE_VALUES = ", ".join(f"'{r.value}'" for r in Role)


class Organization(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    users: Mapped[list["User"]] = relationship(back_populates="organization")


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_VALUES})", name="role_valid"),
        # A super admin belongs to no organization; everyone else belongs to exactly one.
        CheckConstraint(
            "(role = 'super_admin') = (organization_id IS NULL)", name="org_matches_role"
        ),
        Index("ix_users_organization_id_created_at", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    # Stored lower-cased; unique across the platform so login needs only email + password.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.EMPLOYEE.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization | None] = relationship(back_populates="users")


class RefreshToken(UUIDPrimaryKey, Base):
    """One row per issued refresh token. Only a SHA-256 hash of the token is stored.

    Tokens are rotated on every use. All tokens from one login share a `family_id`,
    so if an already-used token is presented again (a sign of theft), the whole
    family is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family_id", "family_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
