"""Auth and tenancy: organizations, users, refresh tokens, audit logs.

Every tenant-owned table carries organization_id and is indexed by it first,
so tenant-scoped queries stay fast as data grows.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 14:44:46.035233
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )
    op.create_table(
        "users",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(role = 'super_admin') = (organization_id IS NULL)",
            name=op.f("ck_users_org_matches_role"),
        ),
        sa.CheckConstraint(
            "role IN ('super_admin', 'org_admin', 'manager', 'employee')",
            name=op.f("ck_users_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_users_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(
        "ix_users_organization_id_created_at",
        "users",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "audit_logs",
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_logs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_organization_id_created_at",
        "audit_logs",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], unique=False)
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_audit_logs_organization_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_users_organization_id_created_at", table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
