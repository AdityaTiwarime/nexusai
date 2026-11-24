"""Writing audit events.

Events are added to the caller's session, so they commit (or roll back) together
with the action they describe: an audit row never claims something that didn't happen.
"""

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_ctx
from app.modules.audit.models import AuditLog


def record(
    session: AsyncSession,
    *,
    action: str,
    organization_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    request: Request | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        ip_address=request.client.host if request and request.client else None,
        request_id=request_id_ctx.get(),
        details=details or {},
    )
    session.add(entry)
    return entry
