"""Import every model module so SQLAlchemy and Alembic see all tables."""

from app.modules.agents.models import AgentRun, ApprovalRequest, OutboxMessage, Ticket
from app.modules.audit.models import AuditLog
from app.modules.auth.models import Organization, RefreshToken, User
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, IngestionJob
from app.modules.integrations.models import Integration, IntegrationCall, WebhookEvent
from app.modules.rag.models import Conversation, Message

__all__ = [
    "AgentRun",
    "ApprovalRequest",
    "OutboxMessage",
    "Ticket",
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentVersion",
    "IngestionJob",
    "Integration",
    "IntegrationCall",
    "Message",
    "Organization",
    "RefreshToken",
    "User",
    "WebhookEvent",
]
