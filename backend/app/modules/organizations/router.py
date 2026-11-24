from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.modules.auth.deps import require_org_member, require_roles
from app.modules.auth.models import Organization, User
from app.modules.auth.roles import Role
from app.modules.auth.schemas import OrganizationOut

router = APIRouter(tags=["organizations"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/organizations/current", response_model=OrganizationOut)
async def current_organization(
    caller: Annotated[User, Depends(require_org_member())], session: Session
) -> Organization:
    return await session.get(Organization, caller.organization_id)


@router.get("/admin/organizations", response_model=list[OrganizationOut])
async def list_all_organizations(
    _: Annotated[User, Depends(require_roles(Role.SUPER_ADMIN))], session: Session
) -> list[Organization]:
    """Platform-wide view, only for the super admin who operates NexusAI itself."""
    return list(await session.scalars(select(Organization).order_by(Organization.created_at)))
