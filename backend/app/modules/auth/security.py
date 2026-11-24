"""Password hashing and token handling.

- Passwords: Argon2id (memory-hard, the current OWASP recommendation).
- Access tokens: short-lived signed JWTs, sent as `Authorization: Bearer`.
- Refresh tokens: long random strings, stored only as SHA-256 hashes, sent in an
  httpOnly cookie so JavaScript (and therefore XSS) cannot read them.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

_hasher = PasswordHasher()

# Verified against when the email is unknown, so "no such user" and "wrong password"
# take the same time and can't be told apart by timing.
_DUMMY_HASH = _hasher.hash("timing-equalisation-only")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    role: str


class InvalidTokenError(Exception):
    pass


def create_access_token(
    *, user_id: uuid.UUID, organization_id: uuid.UUID | None, role: str, settings: Settings
) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires_in = settings.access_token_minutes * 60
    payload = {
        "sub": str(user_id),
        "org": str(organization_id) if organization_id else None,
        "role": role,
        "type": "access",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pin the algorithm: never trust the token's own "alg" header.
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        if payload.get("type") != "access":
            raise InvalidTokenError("wrong token type")
        org = payload.get("org")
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(org) if org else None,
            role=str(payload["role"]),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc


def new_refresh_token() -> tuple[str, str]:
    """Return (raw token for the client, hash for the database)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    # A fast hash is fine here: the token is 384 random bits, so it can't be brute-forced.
    return hashlib.sha256(raw.encode()).hexdigest()
