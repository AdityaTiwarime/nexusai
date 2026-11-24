"""Authentication: registration, login, tokens, refresh rotation, logout."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import DEV_JWT_SECRET
from app.main import create_app
from tests.conftest import API, PASSWORD, bearer, login, make_settings, register_org

# --- registration -----------------------------------------------------------------


async def test_register_creates_org_admin_and_signs_in(client):
    response = await client.post(
        f"{API}/auth/register",
        json={
            "organization_name": "Acme Corp",
            "full_name": "Asha Admin",
            "email": "Asha@Acme.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60

    cookie = response.headers["set-cookie"].lower()
    assert "nexus_refresh=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/v1/auth" in cookie

    me = (await client.get(f"{API}/auth/me", headers=bearer(body["access_token"]))).json()
    assert me["user"]["email"] == "asha@acme.com"  # normalised to lower case
    assert me["user"]["role"] == "org_admin"
    assert me["user"]["last_login_at"] is not None
    assert me["organization"]["name"] == "Acme Corp"
    assert me["organization"]["slug"] == "acme-corp"
    assert "password_hash" not in me["user"]


async def test_register_rejects_duplicate_email(client):
    await register_org(client)
    response = await client.post(
        f"{API}/auth/register",
        json={
            "organization_name": "Other",
            "full_name": "X",
            "email": "ADMIN@acme.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 409


async def test_same_org_name_gets_unique_slug(client):
    await register_org(client, org="Acme Corp", email="a@one.com")
    second = await register_org(client, org="Acme Corp", email="b@two.com")
    me = (await client.get(f"{API}/auth/me", headers=bearer(second.token))).json()
    assert me["organization"]["slug"].startswith("acme-corp-")


@pytest.mark.parametrize(
    "password",
    ["short-pw-1", "x" * 129, "my-asha-password-123"],  # too short, too long, contains email
)
async def test_register_rejects_weak_passwords(client, password):
    response = await client.post(
        f"{API}/auth/register",
        json={
            "organization_name": "Acme",
            "full_name": "Asha",
            "email": "asha@acme.com",
            "password": password,
        },
    )
    assert response.status_code == 422


async def test_public_signup_can_be_disabled(client_factory):
    app = create_app(make_settings(allow_public_signup=False))
    async for client in client_factory(app):
        response = await client.post(
            f"{API}/auth/register",
            json={
                "organization_name": "Acme",
                "full_name": "A",
                "email": "a@acme.com",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 403


# --- login --------------------------------------------------------------------------


async def test_login_is_case_insensitive_on_email(client):
    await register_org(client)
    account = await login(client, "Admin@ACME.com")
    assert account.email == "Admin@ACME.com"


async def test_wrong_password_and_unknown_email_look_identical(client):
    await register_org(client)
    wrong_pw = await client.post(
        f"{API}/auth/login", json={"email": "admin@acme.com", "password": "not-the-password"}
    )
    unknown = await client.post(
        f"{API}/auth/login", json={"email": "nobody@acme.com", "password": "not-the-password"}
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json() == {"detail": "Invalid email or password"}


async def test_failed_login_is_audited(client):
    admin = await register_org(client)
    await client.post(f"{API}/auth/login", json={"email": admin.email, "password": "wrong-one!!"})
    logs = (
        await client.get(
            f"{API}/audit-logs", params={"action": "auth.login_failed"}, headers=bearer(admin.token)
        )
    ).json()
    assert logs["total"] == 1
    assert logs["items"][0]["details"] == {"reason": "bad_password"}


# --- access tokens ------------------------------------------------------------------


async def test_me_requires_a_token(client):
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_tampered_token_is_rejected(client):
    admin = await register_org(client)
    header, payload, signature = admin.token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}xx"
    assert (await client.get(f"{API}/auth/me", headers=bearer(tampered))).status_code == 401


def _forge(claims: dict, key: str = DEV_JWT_SECRET, algorithm: str = "HS256") -> str:
    now = datetime.now(UTC)
    base = {"iss": "nexusai", "type": "access", "iat": now, "exp": now + timedelta(minutes=5)}
    return jwt.encode({**base, **claims}, key, algorithm=algorithm)


async def test_token_signed_with_another_key_is_rejected(client):
    admin = await register_org(client)
    forged = _forge(
        {"sub": admin.user_id, "org": admin.organization_id, "role": "org_admin"},
        key="attacker-controlled-secret-that-is-long-enough",
    )
    assert (await client.get(f"{API}/auth/me", headers=bearer(forged))).status_code == 401


async def test_expired_token_is_rejected(client):
    admin = await register_org(client)
    expired = _forge(
        {
            "sub": admin.user_id,
            "org": admin.organization_id,
            "role": "org_admin",
            "iat": datetime.now(UTC) - timedelta(hours=1),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        }
    )
    assert (await client.get(f"{API}/auth/me", headers=bearer(expired))).status_code == 401


async def test_unsigned_alg_none_token_is_rejected(client):
    admin = await register_org(client)
    unsigned = jwt.encode(
        {"sub": admin.user_id, "iss": "nexusai", "type": "access", "role": "org_admin"},
        key=None,
        algorithm="none",
    )
    assert (await client.get(f"{API}/auth/me", headers=bearer(unsigned))).status_code == 401


# --- refresh tokens -----------------------------------------------------------------


async def test_refresh_rotates_the_token(client):
    await register_org(client)
    old_cookie = client.cookies.get("nexus_refresh")

    response = await client.post(f"{API}/auth/refresh")
    assert response.status_code == 200
    new_cookie = client.cookies.get("nexus_refresh")
    assert new_cookie and new_cookie != old_cookie
    me = await client.get(f"{API}/auth/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200


async def test_reusing_an_old_refresh_token_revokes_the_whole_session(client):
    admin = await register_org(client)
    stolen = client.cookies.get("nexus_refresh")
    assert (await client.post(f"{API}/auth/refresh")).status_code == 200
    current = client.cookies.get("nexus_refresh")

    # An attacker replays the old (already rotated) token...
    client.cookies.set("nexus_refresh", stolen, path="/api/v1/auth")
    replay = await client.post(f"{API}/auth/refresh")
    assert replay.status_code == 401

    # ...so the legitimate user's newer token is revoked too, and the event is audited.
    client.cookies.set("nexus_refresh", current, path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/refresh")).status_code == 401
    logs = (
        await client.get(
            f"{API}/audit-logs",
            params={"action": "auth.refresh_token_reuse_detected"},
            headers=bearer(admin.token),
        )
    ).json()
    # Both revoked tokens count as reuse, so there is at least one detection event.
    assert logs["total"] >= 1


async def test_refresh_without_cookie_fails(client):
    assert (await client.post(f"{API}/auth/refresh")).status_code == 401


async def test_logout_revokes_the_refresh_token(client):
    await register_org(client)
    cookie = client.cookies.get("nexus_refresh")
    response = await client.post(f"{API}/auth/logout")
    assert response.status_code == 204
    assert (
        'nexus_refresh=""' in response.headers["set-cookie"]
        or "Max-Age=0" in response.headers["set-cookie"]
    )

    client.cookies.set("nexus_refresh", cookie, path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/refresh")).status_code == 401


# --- settings guard rails -----------------------------------------------------------


def test_production_rejects_the_development_jwt_secret():
    with pytest.raises(ValueError, match="NEXUS_JWT_SECRET"):
        make_settings(environment="production", cookie_secure=True)


def test_production_requires_secure_cookies():
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        make_settings(environment="production", jwt_secret="x" * 40, cookie_secure=False)


def test_production_accepts_a_strong_secret():
    settings = make_settings(
        environment="production",
        jwt_secret="x" * 40,
        cookie_secure=True,
        encryption_key="cHJvZC10ZXN0LWtleS1ub3QtdGhlLWRldi1vbmUtMTI=",
    )
    assert settings.is_production
