"""Authentication and authorization dependencies.

Authorization is enforced here, on the server, for every request. The frontend
hiding a button is a convenience, never a security control.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings_dep
from app.core.config import Settings
from app.modules.auth.models import Organization, User
from app.modules.auth.roles import Role
from app.modules.auth.security import InvalidTokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User:
    if credentials is None:
        raise _UNAUTHORIZED
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except InvalidTokenError:
        raise _UNAUTHORIZED from None

    # Re-read the user on every request: a deactivated user or a changed role takes
    # effect immediately, not when the 15-minute token expires.
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    if user.organization_id is not None:
        org = await session.get(Organization, user.organization_id)
        if org is None or not org.is_active:
            raise _UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[..., Awaitable[User]]:
    allowed = {r.value for r in roles}

    async def checker(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
        return user

    return checker


def require_org_member() -> Callable[..., Awaitable[User]]:
    """Any user that belongs to an organization (i.e. not the platform super admin)."""

    async def checker(user: CurrentUser) -> User:
        if user.organization_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
        return user

    return checker
