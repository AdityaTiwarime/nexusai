"""Authentication business logic: registration, login, refresh-token rotation, logout."""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.audit import service as audit
from app.modules.auth.models import Organization, RefreshToken, User
from app.modules.auth.roles import Role
from app.modules.auth.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)


class AuthError(Exception):
    """Raised for any authentication failure. Messages are safe to show to users."""


class ConflictError(Exception):
    pass


@dataclass
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "org"


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = _slugify(name)
    slug = base
    while await session.scalar(select(Organization.id).where(Organization.slug == slug)):
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


async def email_taken(session: AsyncSession, email: str) -> bool:
    return bool(await session.scalar(select(User.id).where(User.email == email)))


async def issue_tokens(
    session: AsyncSession,
    user: User,
    settings: Settings,
    family_id: uuid.UUID | None = None,
) -> IssuedTokens:
    access, expires_in = create_access_token(
        user_id=user.id, organization_id=user.organization_id, role=user.role, settings=settings
    )
    raw, token_hash = new_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_days)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    return IssuedTokens(access, expires_in, raw, expires_at)


async def register_organization(
    session: AsyncSession,
    *,
    organization_name: str,
    full_name: str,
    email: str,
    password: str,
    request: Request,
) -> User:
    if await email_taken(session, email):
        raise ConflictError("An account with this email already exists")
    org = Organization(
        name=organization_name.strip(), slug=await _unique_slug(session, organization_name)
    )
    session.add(org)
    await session.flush()
    user = User(
        organization_id=org.id,
        email=email,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        role=Role.ORG_ADMIN.value,
        last_login_at=datetime.now(UTC),  # registering also signs the user in
    )
    session.add(user)
    await session.flush()
    audit.record(
        session,
        action="organization.registered",
        organization_id=org.id,
        actor_id=user.id,
        request=request,
        target_type="organization",
        target_id=org.id,
        details={"name": org.name},
    )
    return user


async def authenticate(
    session: AsyncSession, *, email: str, password: str, request: Request
) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    # verify_password runs even for unknown emails, so response time doesn't reveal which exist.
    password_ok = verify_password(user.password_hash if user else None, password)
    org_active = True
    if user and user.organization_id:
        org_active = bool(
            await session.scalar(
                select(Organization.is_active).where(Organization.id == user.organization_id)
            )
        )

    if not user or not password_ok or not user.is_active or not org_active:
        audit.record(
            session,
            action="auth.login_failed",
            organization_id=user.organization_id if user else None,
            actor_id=user.id if user else None,
            request=request,
            details={"reason": _failure_reason(user, password_ok, org_active)},
        )
        # One generic message for every case: don't tell an attacker which part was wrong.
        raise AuthError("Invalid email or password")

    user.last_login_at = datetime.now(UTC)
    audit.record(
        session,
        action="auth.login",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=request,
    )
    return user


def _failure_reason(user: User | None, password_ok: bool, org_active: bool) -> str:
    if not user:
        return "unknown_email"
    if not password_ok:
        return "bad_password"
    if not user.is_active:
        return "user_inactive"
    return "organization_inactive" if not org_active else "unknown"


async def rotate_refresh_token(
    session: AsyncSession, *, raw_token: str, settings: Settings, request: Request
) -> tuple[User, IssuedTokens]:
    record_ = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if record_ is None:
        raise AuthError("Session expired, please sign in again")

    user = await session.get(User, record_.user_id)
    if record_.revoked_at is not None:
        # A rotated-out token came back: someone may have stolen it. Kill the whole session.
        await revoke_family(session, record_.family_id)
        audit.record(
            session,
            action="auth.refresh_token_reuse_detected",
            organization_id=user.organization_id if user else None,
            actor_id=record_.user_id,
            request=request,
        )
        raise AuthError("Session expired, please sign in again")

    if record_.expires_at <= now or user is None or not user.is_active:
        raise AuthError("Session expired, please sign in again")
    if user.organization_id:
        org = await session.get(Organization, user.organization_id)
        if org is None or not org.is_active:
            raise AuthError("Session expired, please sign in again")

    record_.revoked_at = now
    tokens = await issue_tokens(session, user, settings, family_id=record_.family_id)
    return user, tokens


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def logout(session: AsyncSession, *, raw_token: str | None, request: Request) -> None:
    if not raw_token:
        return
    record_ = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if record_ is None:
        return
    await revoke_family(session, record_.family_id)
    user = await session.get(User, record_.user_id)
    audit.record(
        session,
        action="auth.logout",
        organization_id=user.organization_id if user else None,
        actor_id=record_.user_id,
        request=request,
    )
