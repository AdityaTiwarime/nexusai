import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.modules.audit.models import AuditLog
from app.modules.auth.deps import require_roles
from app.modules.auth.models import User
from app.modules.auth.roles import Role

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_email: str | None = None
    action: str
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    request_id: str | None
    details: dict[str, Any]
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    total: int
    limit: int
    offset: int


@router.get("", response_model=AuditLogPage)
async def list_audit_logs(
    caller: Annotated[User, Depends(require_roles(Role.ORG_ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str | None, Query(max_length=80)] = None,
) -> AuditLogPage:
    filters = [AuditLog.organization_id == caller.organization_id]
    if action:
        filters.append(AuditLog.action == action)

    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    rows = await session.execute(
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id)
        .limit(limit)
        .offset(offset)
    )
    items = []
    for log, email in rows.all():
        item = AuditLogOut.model_validate(log)
        item.actor_email = email
        items.append(item)
    return AuditLogPage(items=items, total=total or 0, limit=limit, offset=offset)
