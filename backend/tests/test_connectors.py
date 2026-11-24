"""Connector framework unit tests: allowlist, retries, backoff, timeouts, encryption."""

import json
import uuid

import httpx
import pytest

from app.core.crypto import SecretBox
from app.modules.integrations import connectors
from app.modules.integrations.connectors import (
    CallRecord,
    ConnectorError,
    EmailConnector,
    ServiceNowConnector,
    check_base_url,
)
from tests.conftest import make_settings

ALLOWED = ["mock-services", ".service-now.com"]


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.service-now.com",
        "http://mock-services:9000/",
        "https://dev1.service-now.com/x",
    ],
)
def test_allowlist_accepts_listed_hosts_and_subdomains(url):
    assert check_base_url(url, ALLOWED) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5432",
        "https://service-now.com.evil.io",
        "https://evilservice-now.com",
        "http://10.0.0.5",
        "file:///etc/passwd",
        "https://user:pass@acme.service-now.com",
    ],
)
def test_allowlist_rejects_everything_else(url):
    with pytest.raises(ValueError):
        check_base_url(url, ALLOWED)


def _servicenow(handler, records=None, **settings) -> ServiceNowConnector:
    async def record(call: CallRecord) -> None:
        records.append(call)

    return ServiceNowConnector(
        organization_id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        base_url="http://mock-services",
        config={},
        secrets={"username": "u", "password": "p"},
        settings=make_settings(**{"integration_retry_base_seconds": 0.5, **settings}),
        transport=httpx.MockTransport(handler),
        recorder=record if records is not None else None,
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(connectors.asyncio, "sleep", fake_sleep)
    return delays


async def test_timeouts_are_retried_then_reported(sleeps):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    records: list[CallRecord] = []
    with pytest.raises(ConnectorError) as info:
        await _servicenow(handler, records).health()
    assert info.value.retryable is True
    assert "timed out" in str(info.value)
    assert len(calls) == 3 and len(sleeps) == 2
    assert 0.25 <= sleeps[0] <= 0.5 and 0.5 <= sleeps[1] <= 1.0  # exponential backoff with jitter
    assert records[0].outcome == "failure" and records[0].attempts == 3


async def test_retry_after_is_honoured_but_capped(sleeps):
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(429, headers={"Retry-After": "999"}),
            httpx.Response(200, json={}),
        ]
    )
    await _servicenow(lambda r: next(responses)).health()
    assert sleeps == [2.0, 10.0]  # capped by integration_max_retry_after_seconds


async def test_client_errors_are_not_retried(sleeps):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "bad"})

    with pytest.raises(ConnectorError) as info:
        await _servicenow(handler).health()
    assert len(calls) == 1 and sleeps == []
    assert info.value.retryable is False and info.value.http_status == 400


async def test_requests_carry_auth_and_idempotency_headers():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(request.headers)
        return httpx.Response(201, json={"result": {"sys_id": "abc", "number": "INC0010001"}})

    result = await _servicenow(handler).create_incident(
        title="t",
        description="d",
        priority="low",
        caller_email=None,
        correlation_id="c",
        idempotency_key="k-1",
    )
    assert seen["authorization"].startswith("Basic ")
    assert seen["idempotency-key"] == "k-1"
    assert result["number"] == "INC0010001" and result["url"].endswith("sys_id=abc")


async def test_redirects_are_not_followed():
    def handler(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    # A 3xx isn't an error status here, but the client must not follow it elsewhere.
    response = await _servicenow(handler)._call("probe", "GET", "/x", retry=False)
    assert response.status_code == 302


async def test_email_connector_uses_graph_style_payload():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = request.read()
        return httpx.Response(202, headers={"X-Message-Id": "<m1>"})

    connector = EmailConnector(
        organization_id=uuid.uuid4(),
        integration_id=uuid.uuid4(),
        base_url="http://mock-services",
        config={"sender": "support@acme.com"},
        secrets={"api_key": "k"},
        settings=make_settings(),
        transport=httpx.MockTransport(handler),
    )
    message_id = await connector.send(to="a@b.com", subject="s", body="b", idempotency_key="k")
    assert message_id == "<m1>"
    assert seen["url"] == "http://mock-services/v1.0/users/support%40acme.com/sendMail"
    payload = json.loads(seen["json"])
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "a@b.com"}}]


def test_secret_box_round_trip_and_wrong_key():
    box = SecretBox(make_settings().encryption_key.get_secret_value())
    token = box.encrypt({"password": "hunter2"})
    assert "hunter2" not in token
    assert box.decrypt(token) == {"password": "hunter2"}
    other = SecretBox("cHJvZC10ZXN0LWtleS1ub3QtdGhlLWRldi1vbmUtMTI=")
    with pytest.raises(ValueError, match="can't be decrypted"):
        other.decrypt(token)
