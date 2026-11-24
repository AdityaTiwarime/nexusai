"""Import every model module so SQLAlchemy and Alembic see all tables."""

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Organization, RefreshToken, User
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, IngestionJob

__all__ = [
    "AuditLog",
    "Document",
    "DocumentChunk",
    "DocumentVersion",
    "IngestionJob",
    "Organization",
    "RefreshToken",
    "User",
]
