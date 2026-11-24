"""Integrations: external connections (encrypted secrets), call log, webhook events;
external references on tickets and delivery details on outbox messages.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24 17:12:33.385491
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=False),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("health_status", sa.String(length=16), nullable=False),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            "health_status IN ('unknown', 'ok', 'error')",
            name=op.f("ck_integrations_health_status_valid"),
        ),
        sa.CheckConstraint(
            "kind IN ('servicenow', 'salesforce', 'email')", name=op.f("ck_integrations_kind_valid")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_integrations_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_integrations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_integrations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        sa.UniqueConstraint("organization_id", "kind", name="uq_integrations_organization_kind"),
    )
    op.create_table(
        "integration_calls",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure')", name=op.f("ck_integration_calls_outcome_valid")
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integrations.id"],
            name=op.f("fk_integration_calls_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_integration_calls_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_calls")),
    )
    op.create_index(
        "ix_integration_calls_integration_id_created_at",
        "integration_calls",
        ["integration_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "webhook_events",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("integration_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integrations.id"],
            name=op.f("fk_webhook_events_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_webhook_events_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint(
            "integration_id", "event_id", name="uq_webhook_events_integration_event"
        ),
    )
    op.add_column(
        "outbox_messages", sa.Column("provider_message_id", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "outbox_messages", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tickets", sa.Column("external_system", sa.String(length=32), nullable=True))
    op.add_column("tickets", sa.Column("external_id", sa.String(length=100), nullable=True))
    op.add_column("tickets", sa.Column("external_number", sa.String(length=64), nullable=True))
    op.add_column("tickets", sa.Column("external_url", sa.String(length=500), nullable=True))
    op.create_index(
        "ix_tickets_external_system_external_id",
        "tickets",
        ["external_system", "external_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_external_system_external_id", table_name="tickets")
    op.drop_column("tickets", "external_url")
    op.drop_column("tickets", "external_number")
    op.drop_column("tickets", "external_id")
    op.drop_column("tickets", "external_system")
    op.drop_column("outbox_messages", "sent_at")
    op.drop_column("outbox_messages", "provider_message_id")
    op.drop_table("webhook_events")
    op.drop_index("ix_integration_calls_integration_id_created_at", table_name="integration_calls")
    op.drop_table("integration_calls")
    op.drop_table("integrations")
