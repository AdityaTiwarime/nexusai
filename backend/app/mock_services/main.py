"""Mock enterprise services: ServiceNow-like ticketing, Salesforce-like CRM and a
Microsoft-Graph-like email API, in one small FastAPI app with SYNTHETIC data.

They exist so NexusAI's connectors can be developed and tested against something
that behaves like the real systems, without accounts or credentials:
- realistic auth: Basic auth (ServiceNow), OAuth2 client credentials (Salesforce),
  bearer API key (email);
- `Idempotency-Key` support, so a retried create returns the same record;
- fault injection (`POST /_mock/faults`): rate limits with Retry-After, 5xx errors,
  "flaky" (fail the next N calls) and slow responses;
- outgoing webhooks signed with HMAC-SHA256 when an incident is resolved.

Run:  uvicorn app.mock_services.main:app --port 9000
This is test infrastructure, not part of the product. State lives in memory.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

SN_USER = os.environ.get("MOCK_SERVICENOW_USER", "nexus")
SN_PASSWORD = os.environ.get("MOCK_SERVICENOW_PASSWORD", "servicenow-demo-password")
SF_CLIENT_ID = os.environ.get("MOCK_SALESFORCE_CLIENT_ID", "nexus-client")
SF_CLIENT_SECRET = os.environ.get("MOCK_SALESFORCE_CLIENT_SECRET", "salesforce-demo-secret")
MAIL_API_KEY = os.environ.get("MOCK_EMAIL_API_KEY", "email-demo-key")

_CONTACT_FIELDS = ("Email", "Name", "Account", "Tier", "Owner")
CONTACTS = [
    dict(zip(_CONTACT_FIELDS, row, strict=True))
    for row in (
        ("jane@globex.com", "Jane Cooper", "Globex Corporation", "Enterprise", "Arjun Mehta"),
        ("bob@initech.com", "Bob Slydell", "Initech", "Business", "Priya Nair"),
        ("maria@umbrella.io", "Maria Santos", "Umbrella Health", "Enterprise", "Arjun Mehta"),
        ("li.wei@stark.dev", "Li Wei", "Stark Logistics", "Starter", "Priya Nair"),
        ("jane@example.com", "Jane Example", "Example Ltd", "Business", "Arjun Mehta"),
    )
]


@dataclass
class Fault:
    mode: str = "ok"  # ok | rate_limit | error | flaky | slow
    remaining: int = 0  # for flaky: how many calls still fail
    retry_after: int = 1
    delay_seconds: float = 0.0


@dataclass
class State:
    incidents: dict[str, dict[str, Any]] = field(default_factory=dict)
    sent_mail: list[dict[str, Any]] = field(default_factory=list)
    idempotency: dict[tuple[str, str], Any] = field(default_factory=dict)
    tokens: dict[str, float] = field(default_factory=dict)
    faults: dict[str, Fault] = field(default_factory=dict)
    webhooks: list[dict[str, str]] = field(default_factory=list)
    webhook_deliveries: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 10000
    calls: dict[str, int] = field(default_factory=dict)


state = State()
# Tests replace this to deliver webhooks in-process.
webhook_transport: httpx.AsyncBaseTransport | None = None

app = FastAPI(title="NexusAI mock enterprise services", docs_url="/docs")


def reset() -> None:
    global state
    state = State()


async def apply_fault(service: str) -> None:
    state.calls[service] = state.calls.get(service, 0) + 1
    fault = state.faults.get(service)
    if fault is None or fault.mode == "ok":
        return
    if fault.mode == "slow":
        await asyncio.sleep(fault.delay_seconds)
        return
    if fault.mode == "flaky":
        if fault.remaining <= 0:
            return
        fault.remaining -= 1
        raise HTTPException(503, "Service temporarily unavailable")
    if fault.mode == "rate_limit":
        raise HTTPException(
            429, "Too many requests", headers={"Retry-After": str(fault.retry_after)}
        )
    raise HTTPException(500, "Internal server error")


# --- fault control and inspection -----------------------------------------------------


class FaultRequest(BaseModel):
    service: str  # servicenow | salesforce | email
    mode: str = "ok"
    count: int = 1
    retry_after: int = 1
    delay_seconds: float = 0.0


@app.post("/_mock/faults")
async def set_fault(body: FaultRequest) -> dict[str, Any]:
    state.faults[body.service] = Fault(body.mode, body.count, body.retry_after, body.delay_seconds)
    return {"service": body.service, "mode": body.mode}


@app.post("/_mock/reset")
async def reset_state() -> dict[str, str]:
    reset()
    return {"status": "reset"}


@app.get("/_mock/state")
async def inspect() -> dict[str, Any]:
    return {
        "incidents": list(state.incidents.values()),
        "sent_mail": state.sent_mail,
        "webhook_deliveries": state.webhook_deliveries,
        "calls": state.calls,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- ServiceNow-like ticketing ----------------------------------------------------------


def servicenow_auth(authorization: str | None = Header(default=None)) -> None:
    expected = "Basic " + base64.b64encode(f"{SN_USER}:{SN_PASSWORD}".encode()).decode()
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Basic"})


class IncidentIn(BaseModel):
    short_description: str
    description: str = ""
    urgency: str = "2"  # 1 high, 2 medium, 3 low (ServiceNow convention)
    caller_email: str | None = None
    correlation_id: str | None = None


@app.get("/api/now/table/sys_user", dependencies=[Depends(servicenow_auth)])
async def servicenow_ping() -> dict[str, Any]:
    await apply_fault("servicenow")
    return {"result": [{"user_name": SN_USER}]}


@app.post("/api/now/table/incident", status_code=201, dependencies=[Depends(servicenow_auth)])
async def create_incident(
    body: IncidentIn, idempotency_key: str | None = Header(default=None)
) -> dict[str, Any]:
    await apply_fault("servicenow")
    if idempotency_key and ("incident", idempotency_key) in state.idempotency:
        return {"result": state.idempotency[("incident", idempotency_key)]}
    state.counter += 1
    incident = {
        "sys_id": uuid.uuid4().hex,
        "number": f"INC{state.counter:07d}",
        "state": "new",
        **body.model_dump(),
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state.incidents[incident["sys_id"]] = incident
    if idempotency_key:
        state.idempotency[("incident", idempotency_key)] = incident
    return {"result": incident}


@app.get("/api/now/table/incident/{sys_id}", dependencies=[Depends(servicenow_auth)])
async def get_incident(sys_id: str) -> dict[str, Any]:
    await apply_fault("servicenow")
    if sys_id not in state.incidents:
        raise HTTPException(404, "Record not found")
    return {"result": state.incidents[sys_id]}


class WebhookRegistration(BaseModel):
    url: str
    secret: str


@app.post("/api/x_nexus/webhooks", status_code=201, dependencies=[Depends(servicenow_auth)])
async def register_webhook(body: WebhookRegistration) -> dict[str, str]:
    """Stands in for the admin setting up an outbound REST message in ServiceNow."""
    state.webhooks = [w for w in state.webhooks if w["url"] != body.url]
    state.webhooks.append({"url": body.url, "secret": body.secret})
    return {"status": "registered"}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


@app.post("/_mock/servicenow/incidents/{number}/resolve")
async def resolve_incident(number: str) -> dict[str, Any]:
    """Simulate an agent resolving the incident in ServiceNow: fires signed webhooks."""
    incident = next((i for i in state.incidents.values() if i["number"] == number), None)
    if incident is None:
        raise HTTPException(404, "Unknown incident")
    incident["state"] = "resolved"
    event = {
        "event_id": uuid.uuid4().hex,
        "event_type": "incident.resolved",
        "incident": {"sys_id": incident["sys_id"], "number": number, "state": "resolved"},
    }
    body = json.dumps(event).encode()
    deliveries = []
    async with httpx.AsyncClient(transport=webhook_transport, timeout=5) as client:
        for hook in state.webhooks:
            timestamp = str(int(time.time()))
            try:
                response = await client.post(
                    hook["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-NexusAI-Timestamp": timestamp,
                        "X-NexusAI-Signature": sign(hook["secret"], timestamp, body),
                    },
                )
                deliveries.append({"url": hook["url"], "status": response.status_code})
            except httpx.HTTPError as exc:
                deliveries.append({"url": hook["url"], "error": type(exc).__name__})
    state.webhook_deliveries.extend(deliveries)
    return {"event": event, "deliveries": deliveries}


# --- Salesforce-like CRM ------------------------------------------------------------------


@app.post("/services/oauth2/token")
async def salesforce_token(
    grant_type: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)
) -> JSONResponse:
    await apply_fault("salesforce")
    if grant_type != "client_credentials" or not (
        hmac.compare_digest(client_id, SF_CLIENT_ID)
        and hmac.compare_digest(client_secret, SF_CLIENT_SECRET)
    ):
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    token = secrets.token_urlsafe(24)
    state.tokens[token] = time.time() + 3600
    return JSONResponse({"access_token": token, "token_type": "Bearer", "expires_in": 3600})


def salesforce_auth(authorization: str | None = Header(default=None)) -> None:
    token = (authorization or "").removeprefix("Bearer ")
    if state.tokens.get(token, 0) < time.time():
        raise HTTPException(401, "INVALID_SESSION_ID")


@app.get("/services/data/v60.0/contacts", dependencies=[Depends(salesforce_auth)])
async def find_contact(email: str) -> dict[str, Any]:
    """Simplified stand-in for a SOQL query on Contact by email."""
    await apply_fault("salesforce")
    records = [c for c in CONTACTS if c["Email"].lower() == email.lower()]
    return {"totalSize": len(records), "records": records}


# --- Microsoft-Graph-like email --------------------------------------------------------------


def email_auth(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {MAIL_API_KEY}"):
        raise HTTPException(401, "Unauthorized")


@app.post("/v1.0/users/{sender}/sendMail", dependencies=[Depends(email_auth)])
async def send_mail(
    sender: str, request: Request, idempotency_key: str | None = Header(default=None)
) -> Response:
    await apply_fault("email")
    payload = await request.json()
    if not (idempotency_key and ("mail", idempotency_key) in state.idempotency):
        message_id = f"<{uuid.uuid4().hex}@mock-mail>"
        state.sent_mail.append({"id": message_id, "from": sender, **payload})
        if idempotency_key:
            state.idempotency[("mail", idempotency_key)] = message_id
    else:
        message_id = state.idempotency[("mail", idempotency_key)]
    # Graph answers 202 Accepted with no body; the id header lets tests correlate.
    return Response(status_code=202, headers={"X-Message-Id": message_id})


@app.get("/v1.0/users/{sender}", dependencies=[Depends(email_auth)])
async def get_sender(sender: str) -> dict[str, str]:
    await apply_fault("email")
    return {"mail": sender}
