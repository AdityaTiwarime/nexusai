"""Auth endpoints: register, login, refresh, logout, me.

The access token is returned in the JSON body (the frontend keeps it in memory).
The refresh token is set as an httpOnly, SameSite=Strict cookie scoped to the
auth endpoints, so page scripts can never read it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings_dep
from app.core.config import Settings
from app.modules.auth import service
from app.modules.auth.deps import CurrentUser
from app.modules.auth.models import Organization
from app.modules.auth.schemas import (
    LoginRequest,
    MeResponse,
    OrganizationOut,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def _cookie_path(settings: Settings) -> str:
    return f"{settings.api_prefix}/auth"


def _set_refresh_cookie(
    response: Response, tokens: service.IssuedTokens, settings: Settings
) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=tokens.refresh_token,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=_cookie_path(settings),
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=_cookie_path(settings),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _token_response(tokens: service.IssuedTokens) -> TokenResponse:
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.expires_in)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: Session,
    settings: SettingsDep,
) -> TokenResponse:
    """Create a new organization with the caller as its first org admin, and sign in."""
    if not settings.allow_public_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Public sign-up is disabled")
    try:
        user = await service.register_organization(
            session,
            organization_name=body.organization_name,
            full_name=body.full_name,
            email=body.email,
            password=body.password,
            request=request,
        )
    except service.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    tokens = await service.issue_tokens(session, user, settings)
    await session.commit()
    _set_refresh_cookie(response, tokens, settings)
    return _token_response(tokens)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Session,
    settings: SettingsDep,
) -> TokenResponse:
    try:
        user = await service.authenticate(
            session, email=body.email, password=body.password, request=request
        )
    except service.AuthError as exc:
        await session.commit()  # keep the failed-login audit record
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    tokens = await service.issue_tokens(session, user, settings)
    await session.commit()
    _set_refresh_cookie(response, tokens, settings)
    return _token_response(tokens)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={401: {"description": "Refresh token missing, expired, revoked or reused"}},
)
async def refresh(
    request: Request,
    response: Response,
    session: Session,
    settings: SettingsDep,
) -> TokenResponse | JSONResponse:
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        _, tokens = await service.rotate_refresh_token(
            session, raw_token=raw, settings=settings, request=request
        )
    except service.AuthError as exc:
        await session.commit()  # keep reuse-detection revocations and audit records
        # Return (not raise) so the cookie-clearing header is actually sent.
        failure = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)}
        )
        _clear_refresh_cookie(failure, settings)
        return failure
    await session.commit()
    _set_refresh_cookie(response, tokens, settings)
    return _token_response(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: Session,
    settings: SettingsDep,
) -> Response:
    await service.logout(
        session, raw_token=request.cookies.get(settings.refresh_cookie_name), request=request
    )
    await session.commit()
    _clear_refresh_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, session: Session) -> MeResponse:
    org = await session.get(Organization, user.organization_id) if user.organization_id else None
    return MeResponse(
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(org) if org else None,
    )
