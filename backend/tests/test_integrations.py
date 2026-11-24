"""Integrations against the in-process mock ServiceNow / Salesforce / email services.

The mock services run as a real ASGI app (app/mock_services) behind httpx's
ASGITransport, so connectors make real HTTP requests (auth headers, status codes,
Retry-After, idempotency keys) without a network. Their webhooks are delivered
back into the NexusAI app the same way.
"""

import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import func, select, text

from app.main import create_app
from app.mock_services import main as mock
from app.modules.agents.models import OutboxMessage, Ticket
from app.modules.integrations.models import Integration
from app.modules.integrations.router import expected_signature
from tests.conftest import API, Account, add_user, bearer, make_settings, register_org

BASE = "http://mock-services"
SERVICENOW = {
    "kind": "servicenow",
    "name": "ServiceNow (prod)",
    "base_url": BASE,
    "secrets": {"username": mock.SN_USER, "password": mock.SN_PASSWORD},
}
SALESFORCE = {
    "kind": "salesforce",
    "name": "Salesforce",
    "base_url": BASE,
    "secrets": {"client_id": mock.SF_CLIENT_ID, "client_secret": mock.SF_CLIENT_SECRET},
}
EMAIL = {
    "kind": "email",
    "name": "Support mailbox",
    "base_url": BASE,
    "config": {"sender": "support@acme.com"},
    "secrets": {"api_key": mock.MAIL_API_KEY},
}
REQUEST = "Customer jane@globex.com can't log in. Open a high priority ticket and email her."


@pytest.fixture
async def ctx(client_factory, tmp_path) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    settings = make_settings(
        storage_dir=str(tmp_path),
        integration_retry_base_seconds=0,
        integration_max_retry_after_seconds=0,
        public_api_url="http://test",
    )
    app = create_app(settings)
    mock.reset()
    async for client in client_factory(app):
        app.state.integrations.transport = httpx.ASGITransport(app=mock.app)
        mock.webhook_transport = httpx.ASGITransport(app=app)
        yield client, app
    mock.webhook_transport = None
    mock.reset()


async def _connect(client, admin: Account, *specs: dict) -> list[dict]:
    created = []
    for spec in specs:
        response = await client.post(f"{API}/integrations", headers=bearer(admin.token), json=spec)
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


async def _team(client) -> tuple[Account, Account]:
    admin = await register_org(client)
    manager = await add_user(client, admin, "manager@acme.com", "manager")
    return admin, manager


async def _run_and_approve(
    client, requester: Account, approver: Account, request: str = REQUEST
) -> dict:
    run = (
        await client.post(
            f"{API}/agents/runs", headers=bearer(requester.token), json={"request": request}
        )
    ).json()
    response = await client.post(
        f"{API}/approvals/{run['approval']['id']}/approve", headers=bearer(approver.token), json={}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _calls(client, admin, integration_id) -> list[dict]:
    return (
        await client.get(f"{API}/integrations/{integration_id}/calls", headers=bearer(admin.token))
    ).json()


# --- managing integrations ---------------------------------------------------------------


async def test_connecting_servicenow(ctx):
    client, app = ctx
    admin = await register_org(client)
    [created] = await _connect(client, admin, SERVICENOW)
    assert created["health_status"] == "ok"
    assert created["secret_fields"] == ["password", "username"]
    assert created["webhook_url"] == f"http://test/api/v1/webhooks/servicenow/{created['id']}"
    assert len(created["webhook_secret"]) >= 40  # shown once
    assert created["config"]["webhook_registered"] is True
    assert mock.state.webhooks[0]["url"] == created["webhook_url"]

    listed = (await client.get(f"{API}/integrations", headers=bearer(admin.token))).json()
    assert "webhook_secret" not in listed[0]
    assert mock.SN_PASSWORD not in json.dumps(listed)

    # The password is stored encrypted, never in plain text.
    async with app.state.db.session_factory() as session:
        stored = await session.scalar(select(Integration.secrets_encrypted))
    assert mock.SN_PASSWORD not in stored and mock.SN_USER not in stored


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"base_url": "http://169.254.169.254/latest"}, "not allowed"),
        ({"base_url": "http://internal-db:5432"}, "not allowed"),
        ({"base_url": "ftp://mock-services"}, "http:// or https://"),
        ({"base_url": "http://user:pw@mock-services"}, "credential fields"),
        ({"secrets": {"username": "x"}}, "password"),
        ({"secrets": {**SERVICENOW["secrets"], "token": "x"}}, "Extra inputs"),
    ],
)
async def test_invalid_integrations_are_rejected(ctx, change, message):
    client, _ = ctx
    admin = await register_org(client)
    response = await client.post(
        f"{API}/integrations", headers=bearer(admin.token), json={**SERVICENOW, **change}
    )
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert mock.state.calls == {}  # nothing was contacted


async def test_one_integration_per_kind_and_admin_only(ctx):
    client, _ = ctx
    admin, manager = await _team(client)
    await _connect(client, admin, SERVICENOW)
    assert (
        await client.post(f"{API}/integrations", headers=bearer(admin.token), json=SERVICENOW)
    ).status_code == 409
    assert (
        await client.post(f"{API}/integrations", headers=bearer(manager.token), json=EMAIL)
    ).status_code == 403
    assert len((await client.get(f"{API}/integrations", headers=bearer(manager.token))).json()) == 1
    employee = await add_user(client, admin, "emp@acme.com", "employee")
    assert (
        await client.get(f"{API}/integrations", headers=bearer(employee.token))
    ).status_code == 403


async def test_wrong_credentials_show_as_unhealthy_and_can_be_fixed(ctx):
    client, _ = ctx
    admin = await register_org(client)
    bad = {**SERVICENOW, "secrets": {"username": "nexus", "password": "wrong"}}
    [created] = await _connect(client, admin, bad)
    assert created["health_status"] == "error"
    assert "HTTP 401" in created["last_error"]
    calls = await _calls(client, admin, created["id"])
    assert calls[0]["attempts"] == 1  # a 401 isn't retried

    fixed = await client.patch(
        f"{API}/integrations/{created['id']}",
        headers=bearer(admin.token),
        json={"secrets": SERVICENOW["secrets"]},
    )
    assert fixed.json()["health_status"] == "ok" and fixed.json()["last_error"] is None
    logs = (await client.get(f"{API}/audit-logs", headers=bearer(admin.token))).json()["items"]
    update = next(e for e in logs if e["action"] == "integration.updated")
    assert update["details"]["changed"] == ["secrets"]
    assert "servicenow-demo-password" not in json.dumps(logs)


async def test_other_organizations_cannot_touch_an_integration(ctx):
    client, _ = ctx
    admin = await register_org(client)
    [created] = await _connect(client, admin, EMAIL)
    outsider = await register_org(client, "Other Org", "boss@other.com")
    headers = bearer(outsider.token)
    url = f"{API}/integrations/{created['id']}"
    assert (await client.get(f"{API}/integrations", headers=headers)).json() == []
    assert (await client.post(f"{url}/test", headers=headers)).status_code == 404
    assert (await client.patch(url, headers=headers, json={"name": "x"})).status_code == 404
    assert (await client.get(f"{url}/calls", headers=headers)).status_code == 404
    assert (await client.delete(url, headers=headers)).status_code == 404


# --- agents through connectors -------------------------------------------------------------


async def test_agent_actions_reach_the_external_systems(ctx):
    client, app = ctx
    admin, manager = await _team(client)
    await _connect(client, admin, SERVICENOW, SALESFORCE, EMAIL)

    run = await _run_and_approve(client, admin, manager)
    assert run["status"] == "completed", run
    lookup, ticket, email = run["steps"]
    assert lookup["result"]["name"] == "Jane Cooper" and lookup["result"]["tier"] == "Enterprise"
    assert ticket["result"]["external_system"] == "servicenow"
    number = ticket["result"]["external_number"]
    assert number.startswith("INC")
    assert email["result"]["status"] == "sent"
    assert f"ServiceNow {number}" in run["summary"]
    assert "Customer: Jane Cooper (Globex Corporation, Enterprise)." in run["summary"]
    assert "Sent an email to jane@globex.com." in run["summary"]

    [incident] = mock.state.incidents.values()
    assert incident["number"] == number
    assert incident["urgency"] == "1"  # high priority
    assert incident["caller_email"] == "jane@globex.com"
    assert incident["correlation_id"] == run["id"]
    [mail] = mock.state.sent_mail
    assert mail["from"] == "support@acme.com"
    assert number in mail["message"]["subject"]  # the customer sees the ServiceNow number
    assert mail["message"]["toRecipients"][0]["emailAddress"]["address"] == "jane@globex.com"

    async with app.state.db.session_factory() as session:
        stored = await session.scalar(select(Ticket))
        outbox = await session.scalar(select(OutboxMessage))
    assert (stored.external_id, stored.external_number) == (incident["sys_id"], number)
    assert outbox.status == "sent" and outbox.provider_message_id == mail["id"]


async def test_temporary_failures_are_retried_without_duplicates(ctx):
    client, _ = ctx
    admin, manager = await _team(client)
    [servicenow] = await _connect(client, admin, SERVICENOW)
    mock.state.faults["servicenow"] = mock.Fault(mode="flaky", remaining=2)

    run = await _run_and_approve(client, admin, manager, "Open a ticket about the VPN outage")
    assert run["status"] == "completed"
    assert len(mock.state.incidents) == 1
    create = next(
        c
        for c in await _calls(client, admin, servicenow["id"])
        if c["operation"] == "create_incident"
    )
    assert (create["outcome"], create["attempts"], create["http_status"]) == ("success", 3, 201)


async def test_rate_limited_system_fails_the_step_and_a_retry_succeeds_once(ctx):
    client, app = ctx
    admin, manager = await _team(client)
    [servicenow] = await _connect(client, admin, SERVICENOW)
    mock.state.faults["servicenow"] = mock.Fault(mode="rate_limit", retry_after=30)

    run = await _run_and_approve(client, admin, manager, "Open a ticket about the VPN outage")
    assert run["status"] == "failed"
    assert run["steps"][0]["error"] == "servicenow create_incident failed: returned HTTP 429"
    async with app.state.db.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Ticket)) == 0
    failed = (await _calls(client, admin, servicenow["id"]))[0]
    assert (failed["outcome"], failed["attempts"], failed["http_status"]) == ("failure", 3, 429)

    mock.state.faults["servicenow"] = mock.Fault()  # the system recovers
    retried = await client.post(
        f"{API}/agents/runs/{run['id']}/retry", headers=bearer(manager.token)
    )
    assert retried.json()["status"] == "completed"
    assert len(mock.state.incidents) == 1


async def test_repeated_creates_with_the_same_key_return_the_same_incident(ctx):
    client, app = ctx
    admin = await register_org(client)
    await _connect(client, admin, SERVICENOW)
    async with app.state.db.session_factory() as session:
        integration = await session.scalar(select(Integration))
    connector = app.state.integrations.connector(integration)
    args = {
        "title": "VPN down",
        "description": "x",
        "priority": "high",
        "caller_email": None,
        "correlation_id": "c",
        "idempotency_key": "nexusai-run-1",
    }
    first, second = await connector.create_incident(**args), await connector.create_incident(**args)
    assert first == second and len(mock.state.incidents) == 1


async def test_without_connections_tools_fall_back_to_internal_records(ctx):
    client, _ = ctx
    admin, manager = await _team(client)
    [email] = await _connect(client, admin, EMAIL)
    await client.patch(
        f"{API}/integrations/{email['id']}",
        headers=bearer(admin.token),
        json={"status": "disabled"},
    )
    run = await _run_and_approve(client, admin, manager)
    lookup, ticket, mail = run["steps"]
    assert lookup["result"] == {
        "found": False,
        "email": "jane@globex.com",
        "note": "Salesforce is not connected.",
    }
    assert "external_number" not in ticket["result"]
    assert mail["result"]["status"] == "recorded"
    assert mock.state.sent_mail == [] and mock.state.incidents == {}


async def test_expired_salesforce_token_is_refreshed(ctx):
    client, _ = ctx
    admin = await register_org(client)
    [salesforce] = await _connect(client, admin, SALESFORCE)
    mock.state.tokens.clear()  # every issued token is now invalid
    run = (
        await client.post(
            f"{API}/agents/runs",
            headers=bearer(admin.token),
            json={"request": "Email jane@globex.com an update"},
        )
    ).json()
    assert run["steps"][0]["result"]["found"] is True
    operations = [c["operation"] for c in await _calls(client, admin, salesforce["id"])]
    assert operations.count("oauth_token") >= 2


# --- webhooks ----------------------------------------------------------------------------


async def test_resolving_the_incident_updates_the_ticket_through_a_signed_webhook(ctx):
    client, _ = ctx
    admin, manager = await _team(client)
    await _connect(client, admin, SERVICENOW)
    run = await _run_and_approve(client, admin, manager, "Open a ticket about the VPN outage")
    number = run["steps"][0]["result"]["external_number"]

    # Someone resolves the incident in ServiceNow, which then calls NexusAI's webhook.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock.app), base_url=BASE
    ) as mock_client:
        result = (await mock_client.post(f"/_mock/servicenow/incidents/{number}/resolve")).json()
    assert result["deliveries"][0]["status"] == 200

    tickets = (await client.get(f"{API}/tickets", headers=bearer(admin.token))).json()
    assert tickets[0]["status"] == "resolved"
    logs = (await client.get(f"{API}/audit-logs", headers=bearer(admin.token))).json()["items"]
    assert any(e["action"] == "integration.webhook_received" for e in logs)


def _signed(secret: str, event: dict, timestamp: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    ts = str(timestamp if timestamp is not None else int(time.time()))
    return body, {
        "Content-Type": "application/json",
        "X-NexusAI-Timestamp": ts,
        "X-NexusAI-Signature": expected_signature(secret, ts, body),
    }


async def test_webhook_verification(ctx):
    client, _ = ctx
    admin = await register_org(client)
    [created] = await _connect(client, admin, SERVICENOW)
    url, secret = f"{API}/webhooks/servicenow/{created['id']}", created["webhook_secret"]
    event = {
        "event_id": "evt-1",
        "event_type": "incident.updated",
        "incident": {"sys_id": "x", "state": "in_progress"},
    }

    body, headers = _signed(secret, event)
    assert (await client.post(url, content=body, headers=headers)).json() == {"status": "processed"}
    # Redelivery of the same event is acknowledged but not applied twice.
    assert (await client.post(url, content=body, headers=headers)).json() == {"status": "duplicate"}

    body, headers = _signed("wrong-secret", {**event, "event_id": "evt-2"})
    assert (await client.post(url, content=body, headers=headers)).status_code == 401
    body, headers = _signed(
        secret, {**event, "event_id": "evt-3"}, timestamp=int(time.time()) - 3600
    )
    assert (await client.post(url, content=body, headers=headers)).status_code == 401  # replayed
    body, headers = _signed(secret, {**event, "event_id": "evt-4"})
    tampered = body.replace(b"in_progress", b"resolved")
    assert (await client.post(url, content=tampered, headers=headers)).status_code == 401
    assert (
        await client.post(url, content=body, headers={"Content-Type": "application/json"})
    ).status_code == 401
    body, headers = _signed(secret, {**event, "event_id": "evt-5", "padding": "x" * 70_000})
    assert (await client.post(url, content=body, headers=headers)).status_code == 413
    body, headers = _signed(secret, {"no": "event id"})
    assert (await client.post(url, content=body, headers=headers)).status_code == 400
    unknown = f"{API}/webhooks/servicenow/{uuid.uuid4()}"
    assert (await client.post(unknown, content=body, headers=headers)).status_code == 404


async def test_rotating_the_webhook_secret_invalidates_the_old_one(ctx):
    client, _ = ctx
    admin = await register_org(client)
    [created] = await _connect(client, admin, SERVICENOW)
    rotated = (
        await client.post(
            f"{API}/integrations/{created['id']}/rotate-webhook-secret", headers=bearer(admin.token)
        )
    ).json()
    url = f"{API}/webhooks/servicenow/{created['id']}"
    event = {"event_id": "evt-9", "event_type": "incident.updated", "incident": {}}
    body, headers = _signed(created["webhook_secret"], event)
    assert (await client.post(url, content=body, headers=headers)).status_code == 401
    body, headers = _signed(rotated["webhook_secret"], event)
    assert (await client.post(url, content=body, headers=headers)).status_code == 200
    assert mock.state.webhooks[0]["secret"] == rotated["webhook_secret"]


async def test_disabled_integrations_do_not_accept_webhooks(ctx):
    client, app = ctx
    admin = await register_org(client)
    [created] = await _connect(client, admin, SERVICENOW)
    await client.patch(
        f"{API}/integrations/{created['id']}",
        headers=bearer(admin.token),
        json={"status": "disabled"},
    )
    body, headers = _signed(
        created["webhook_secret"], {"event_id": "e", "event_type": "incident.updated"}
    )
    response = await client.post(
        f"{API}/webhooks/servicenow/{created['id']}", content=body, headers=headers
    )
    assert response.status_code == 404
    async with app.state.db.session_factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM webhook_events")) == 0
