"""Baseline: start of the migration history.

Phase 1 has no business tables yet. This revision anchors the migration chain so
Phase 2 (organizations, users, roles, audit_logs) and Phase 3 (pgvector extension,
documents, chunks) build on a known starting point.

Revision ID: 0001
Revises:
Create Date: 2026-11-24
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
