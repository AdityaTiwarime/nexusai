"""User management inside one organization.

Tenant isolation rule: every query filters by the *caller's* organization_id,
taken from the database (never from the request). A user ID from another
organization therefore behaves exactly like a non-existent ID (404), which also
avoids confirming that it exists.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.modules.audit import service as audit
from app.modules.auth import service as auth_service
from app.modules.auth.deps import require_roles
from app.modules.auth.models import User
from app.modules.auth.roles import Role
from app.modules.auth.schemas import UserCreate, UserOut, UserUpdate
from app.modules.auth.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])

Session = Annotated[AsyncSession, Depends(get_session)]
OrgAdmin = Annotated[User, Depends(require_roles(Role.ORG_ADMIN))]
ManagerOrAbove = Annotated[User, Depends(require_roles(Role.ORG_ADMIN, Role.MANAGER))]


async def _get_in_org(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.organization_id == org_id)
    )
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("", response_model=list[UserOut])
async def list_users(caller: ManagerOrAbove, session: Session) -> list[User]:
    result = await session.scalars(
        select(User).where(User.organization_id == caller.organization_id).order_by(User.created_at)
    )
    return list(result)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, caller: ManagerOrAbove, session: Session) -> User:
    return await _get_in_org(session, user_id, caller.organization_id)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate, caller: OrgAdmin, session: Session, request: Request
) -> User:
    if await auth_service.email_taken(session, body.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(
        organization_id=caller.organization_id,
        email=body.email,
        full_name=body.full_name.strip(),
        password_hash=hash_password(body.password),
        role=body.role.value,
    )
    session.add(user)
    await session.flush()
    audit.record(
        session,
        action="user.created",
        organization_id=caller.organization_id,
        actor_id=caller.id,
        request=request,
        target_type="user",
        target_id=user.id,
        details={"email": user.email, "role": user.role},
    )
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    caller: OrgAdmin,
    session: Session,
    request: Request,
) -> User:
    user = await _get_in_org(session, user_id, caller.organization_id)
    if user.id == caller.id and (body.role is not None or body.is_active is not None):
        # Prevents an admin from locking the organization out by demoting/disabling themselves.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "You cannot change your own role or status"
        )

    changes: dict[str, dict[str, object]] = {}
    if body.full_name is not None and body.full_name.strip() != user.full_name:
        changes["full_name"] = {"from": user.full_name, "to": body.full_name.strip()}
        user.full_name = body.full_name.strip()
    if body.role is not None and body.role.value != user.role:
        changes["role"] = {"from": user.role, "to": body.role.value}
        user.role = body.role.value
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = {"from": user.is_active, "to": body.is_active}
        user.is_active = body.is_active
        if not body.is_active:
            # Deactivation also ends every active session of that user.
            await auth_service.revoke_all_for_user(session, user.id)

    if changes:
        audit.record(
            session,
            action="user.updated",
            organization_id=caller.organization_id,
            actor_id=caller.id,
            request=request,
            target_type="user",
            target_id=user.id,
            details=changes,
        )
        await session.commit()
        await session.refresh(user)
    return user
