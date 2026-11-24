"""Import every model module so SQLAlchemy and Alembic see all tables."""

from app.modules.audit.models import AuditLog
from app.modules.auth.models import Organization, RefreshToken, User
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, IngestionJob
from app.modules.rag.models import Conversation, Message

__all__ = [
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentVersion",
    "IngestionJob",
    "Message",
    "Organization",
    "RefreshToken",
    "User",
]
