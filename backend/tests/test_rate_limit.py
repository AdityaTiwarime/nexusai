"""Login and sign-up rate limiting, against a real Redis (NEXUS_TEST_REDIS_URL)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis

from app.main import create_app
from tests.conftest import API, PASSWORD, TEST_REDIS_URL, make_settings, register_org


@pytest.fixture
async def limited_client(client_factory, tmp_path) -> AsyncIterator[httpx.AsyncClient]:
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.flushdb()
    settings = make_settings(
        rate_limit_enabled=True,
        login_rate_limit_per_ip=8,
        login_failures_per_account=3,
        register_rate_limit_per_ip=2,
        storage_dir=str(tmp_path),
    )
    try:
        async for client in client_factory(create_app(settings)):
            yield client
    finally:
        await redis.flushdb()
        await redis.aclose()


async def _login(client, email, password=PASSWORD):
    return await client.post(f"{API}/auth/login", json={"email": email, "password": password})


async def test_repeated_failures_lock_the_account_temporarily(limited_client):
    await register_org(limited_client)
    for _ in range(3):
        assert (
            await _login(limited_client, "admin@acme.com", "wrong-password-123")
        ).status_code == 401
    # Even the right password is refused while the account is cooling down.
    blocked = await _login(limited_client, "admin@acme.com")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    # Other accounts are unaffected.
    assert (
        await _login(limited_client, "someone@else.com", "wrong-password-123")
    ).status_code == 401


async def test_successful_logins_do_not_count_as_failures(limited_client):
    await register_org(limited_client)
    for _ in range(5):
        assert (await _login(limited_client, "admin@acme.com")).status_code == 200


async def test_login_attempts_are_limited_per_ip(limited_client):
    for i in range(8):
        assert (
            await _login(limited_client, f"user{i}@acme.com", "wrong-password-123")
        ).status_code == 401
    assert (
        await _login(limited_client, "another@acme.com", "wrong-password-123")
    ).status_code == 429


async def test_registration_is_limited_per_ip(limited_client):
    await register_org(limited_client, "Org One", "one@one.com")
    await register_org(limited_client, "Org Two", "two@two.com")
    response = await limited_client.post(
        f"{API}/auth/register",
        json={
            "organization_name": "Org Three",
            "full_name": "Three",
            "email": "three@three.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 429


async def test_redis_down_fails_open(client_factory, tmp_path):
    settings = make_settings(
        rate_limit_enabled=True,
        redis_url="redis://localhost:1/0",  # nothing listens here
        login_failures_per_account=1,
        storage_dir=str(tmp_path),
    )
    async for client in client_factory(create_app(settings)):
        await register_org(client)
        for _ in range(3):
            assert (await _login(client, "admin@acme.com", "wrong-password-123")).status_code == 401
        assert (await _login(client, "admin@acme.com")).status_code == 200

        ready = (await client.get(f"{API}/health/ready")).json()
        assert ready["status"] == "ok"  # Redis is not critical
        assert ready["checks"]["redis"]["status"] == "error"
        assert ready["checks"]["redis"]["critical"] is False


async def test_ready_reports_redis_when_available(limited_client):
    ready = (await limited_client.get(f"{API}/health/ready")).json()
    assert ready["checks"]["redis"]["status"] == "ok"
