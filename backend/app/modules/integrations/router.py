"""Integration management (org admins), call log, and the webhook receiver.

Webhook security (POST /webhooks/servicenow/{integration_id}):
- HMAC-SHA256 over "<timestamp>.<raw body>" with a per-integration secret,
  compared in constant time;
- the timestamp must be within 5 minutes (stops replays of old captured requests);
- each event id is stored once (redelivery is acknowledged but not re-applied);
- the body is size-limited and only parsed after the signature checks out.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.modules.agents.models import Ticket
from app.modules.audit import service as audit
from app.modules.auth.deps import require_roles
from app.modules.auth.models import User
from app.modules.auth.roles import Role
from app.modules.integrations.connectors import ConnectorError, check_base_url
from app.modules.integrations.models import Integration, IntegrationCall, WebhookEvent
from app.modules.integrations.runtime import KIND_LABELS, SCHEMAS, IntegrationRuntime

logger = logging.getLogger("nexusai.integrations")
router = APIRouter(tags=["integrations"])

Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(require_roles(Role.ORG_ADMIN))]
Viewer = Annotated[User, Depends(require_roles(Role.ORG_ADMIN, Role.MANAGER))]

WEBHOOK_TOLERANCE_SECONDS = 300
WEBHOOK_MAX_BYTES = 64 * 1024


def get_runtime(request: Request) -> IntegrationRuntime:
    return request.app.state.integrations


Runtime = Annotated[IntegrationRuntime, Depends(get_runtime)]


# --- schemas ----------------------------------------------------------------------------


class IntegrationCreate(BaseModel):
    kind: Literal["servicenow", "salesforce", "email"]
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=500)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any]


class IntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    config: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None  # replaces all secrets when given
    status: Literal["active", "disabled"] | None = None


class IntegrationOut(BaseModel):
    id: uuid.UUID
    kind: str
    label: str
    name: str
    base_url: str
    config: dict[str, Any]
    secret_fields: list[str]  # which secrets are set; values are never returned
    status: str
    health_status: str
    health_checked_at: datetime | None
    last_error: str | None
    webhook_url: str | None
    created_at: datetime


class IntegrationCreated(IntegrationOut):
    # Shown once, so it can be configured in the other system. Not retrievable later.
    webhook_secret: str | None = None


class CallOut(BaseModel):
    id: uuid.UUID
    operation: str
    outcome: str
    http_status: int | None
    attempts: int
    latency_ms: int
    error: str | None
    created_at: datetime


# --- helpers ----------------------------------------------------------------------------


def _out(integration: Integration, runtime: IntegrationRuntime) -> IntegrationOut:
    return IntegrationOut(
        id=integration.id,
        kind=integration.kind,
        label=KIND_LABELS[integration.kind],
        name=integration.name,
        base_url=integration.base_url,
        config=integration.config,
        secret_fields=sorted(runtime.box.decrypt(integration.secrets_encrypted)),
        status=integration.status,
        health_status=integration.health_status,
        health_checked_at=integration.health_checked_at,
        last_error=integration.last_error,
        webhook_url=runtime.webhook_url(integration.id)
        if integration.kind == "servicenow"
        else None,
        created_at=integration.created_at,
    )


def _validate(
    kind: str,
    base_url: str,
    config: dict[str, Any],
    secrets_: dict[str, Any],
    runtime: IntegrationRuntime,
):
    config_model, secrets_model = SCHEMAS[kind]
    try:
        url = check_base_url(base_url, runtime.settings.integration_allowed_hosts)
        cfg = config_model.model_validate(config).model_dump(mode="json")
        sec = secrets_model.model_validate(secrets_).model_dump(mode="json")
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problems) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return url, cfg, sec


async def _get(session: AsyncSession, integration_id: uuid.UUID, user: User) -> Integration:
    integration = await session.scalar(
        select(Integration).where(
            Integration.id == integration_id, Integration.organization_id == user.organization_id
        )
    )
    if integration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    return integration


async def _check_health(integration: Integration, runtime: IntegrationRuntime) -> None:
    try:
        await runtime.connector(integration).health()
        integration.health_status, integration.last_error = "ok", None
    except ConnectorError as exc:
        integration.health_status, integration.last_error = "error", str(exc)[:500]
    except ValueError as exc:  # e.g. the host was removed from the allowlist since
        integration.health_status, integration.last_error = "error", str(exc)[:500]
    integration.health_checked_at = datetime.now(UTC)


async def _register_webhook(
    integration: Integration, secret: str, runtime: IntegrationRuntime
) -> None:
    """Best effort: real ServiceNow is configured by its admin with the URL and secret."""
    try:
        await runtime.connector(integration).register_webhook(
            runtime.webhook_url(integration.id), secret
        )
        integration.config = {**integration.config, "webhook_registered": True}
    except (ConnectorError, ValueError):
        integration.config = {**integration.config, "webhook_registered": False}


# --- endpoints --------------------------------------------------------------------------


@router.get("/integrations", response_model=list[IntegrationOut])
async def list_integrations(
    caller: Viewer, session: Session, runtime: Runtime
) -> list[IntegrationOut]:
    rows = await session.scalars(
        select(Integration)
        .where(Integration.organization_id == caller.organization_id)
        .order_by(Integration.kind)
    )
    return [_out(i, runtime) for i in rows]


@router.post(
    "/integrations", response_model=IntegrationCreated, status_code=status.HTTP_201_CREATED
)
async def create_integration(
    body: IntegrationCreate, request: Request, caller: Admin, session: Session, runtime: Runtime
) -> IntegrationCreated:
    url, cfg, sec = _validate(body.kind, body.base_url, body.config, body.secrets, runtime)
    exists = await session.scalar(
        select(Integration.id).where(
            Integration.organization_id == caller.organization_id, Integration.kind == body.kind
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A {KIND_LABELS[body.kind]} integration already exists"
        )
    webhook_secret = secrets.token_urlsafe(32) if body.kind == "servicenow" else None
    integration = Integration(
        id=uuid.uuid4(),
        organization_id=caller.organization_id,
        kind=body.kind,
        name=body.name.strip(),
        base_url=url,
        config=cfg,
        secrets_encrypted=runtime.box.encrypt(sec),
        webhook_secret_encrypted=runtime.box.encrypt({"secret": webhook_secret})
        if webhook_secret
        else None,
        status="active",
        health_status="unknown",
        created_by=caller.id,
    )
    session.add(integration)
    audit.record(
        session,
        action="integration.created",
        organization_id=caller.organization_id,
        actor_id=caller.id,
        request=request,
        target_type="integration",
        target_id=integration.id,
        details={"kind": integration.kind, "base_url": url},
    )
    # Commit before contacting the system: the call log references this row and is
    # written in its own transaction, which can only see committed rows.
    try:
        await session.commit()
    except IntegrityError:  # two admins connecting the same system at the same moment
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A {KIND_LABELS[body.kind]} integration already exists"
        ) from None
    await _check_health(integration, runtime)
    if webhook_secret and integration.health_status == "ok":
        await _register_webhook(integration, webhook_secret, runtime)
    await session.commit()
    await session.refresh(integration)
    return IntegrationCreated(
        **_out(integration, runtime).model_dump(), webhook_secret=webhook_secret
    )


@router.patch("/integrations/{integration_id}", response_model=IntegrationOut)
async def update_integration(
    integration_id: uuid.UUID,
    body: IntegrationUpdate,
    request: Request,
    caller: Admin,
    session: Session,
    runtime: Runtime,
) -> IntegrationOut:
    integration = await _get(session, integration_id, caller)
    current_secrets = runtime.box.decrypt(integration.secrets_encrypted)
    url, cfg, sec = _validate(
        integration.kind,
        body.base_url or integration.base_url,
        body.config
        if body.config is not None
        else {k: v for k, v in integration.config.items() if k != "webhook_registered"},
        body.secrets if body.secrets is not None else current_secrets,
        runtime,
    )
    changed = []
    if body.name and body.name.strip() != integration.name:
        integration.name = body.name.strip()
        changed.append("name")
    if url != integration.base_url:
        integration.base_url = url
        changed.append("base_url")
    if body.config is not None:
        # Keep internal flags (e.g. webhook_registered) that aren't user-editable.
        internal = {k: v for k, v in integration.config.items() if k == "webhook_registered"}
        integration.config = {**cfg, **internal}
        changed.append("config")
    if body.secrets is not None:
        integration.secrets_encrypted = runtime.box.encrypt(sec)
        changed.append("secrets")  # which fields changed is logged, never the values
    if body.status and body.status != integration.status:
        integration.status = body.status
        changed.append("status")
    if changed:
        if {"base_url", "secrets", "config"} & set(changed):
            await _check_health(integration, runtime)
        audit.record(
            session,
            action="integration.updated",
            organization_id=caller.organization_id,
            actor_id=caller.id,
            request=request,
            target_type="integration",
            target_id=integration.id,
            details={"changed": changed, "status": integration.status},
        )
        await session.commit()
        await session.refresh(integration)
    return _out(integration, runtime)


@router.delete("/integrations/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: uuid.UUID, request: Request, caller: Admin, session: Session
) -> Response:
    integration = await _get(session, integration_id, caller)
    audit.record(
        session,
        action="integration.deleted",
        organization_id=caller.organization_id,
        actor_id=caller.id,
        request=request,
        target_type="integration",
        target_id=integration.id,
        details={"kind": integration.kind},
    )
    await session.delete(integration)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/integrations/{integration_id}/test", response_model=IntegrationOut)
async def test_integration(
    integration_id: uuid.UUID, caller: Admin, session: Session, runtime: Runtime
) -> IntegrationOut:
    integration = await _get(session, integration_id, caller)
    await _check_health(integration, runtime)
    await session.commit()
    await session.refresh(integration)
    return _out(integration, runtime)


@router.post(
    "/integrations/{integration_id}/rotate-webhook-secret", response_model=IntegrationCreated
)
async def rotate_webhook_secret(
    integration_id: uuid.UUID, request: Request, caller: Admin, session: Session, runtime: Runtime
) -> IntegrationCreated:
    integration = await _get(session, integration_id, caller)
    if integration.kind != "servicenow":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only ServiceNow integrations receive webhooks"
        )
    secret = secrets.token_urlsafe(32)
    integration.webhook_secret_encrypted = runtime.box.encrypt({"secret": secret})
    await _register_webhook(integration, secret, runtime)
    audit.record(
        session,
        action="integration.webhook_secret_rotated",
        organization_id=caller.organization_id,
        actor_id=caller.id,
        request=request,
        target_type="integration",
        target_id=integration.id,
    )
    await session.commit()
    await session.refresh(integration)
    return IntegrationCreated(**_out(integration, runtime).model_dump(), webhook_secret=secret)


@router.get("/integrations/{integration_id}/calls", response_model=list[CallOut])
async def list_calls(
    integration_id: uuid.UUID, caller: Viewer, session: Session
) -> list[IntegrationCall]:
    integration = await _get(session, integration_id, caller)
    rows = await session.scalars(
        select(IntegrationCall)
        .where(IntegrationCall.integration_id == integration.id)
        .order_by(IntegrationCall.created_at.desc())
        .limit(50)
    )
    return list(rows)


# --- webhook receiver -------------------------------------------------------------------


def expected_signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


_REJECTED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")


@router.post("/webhooks/servicenow/{integration_id}")
async def servicenow_webhook(
    integration_id: uuid.UUID, request: Request, session: Session, runtime: Runtime
) -> dict[str, str]:
    integration = await session.get(Integration, integration_id)
    if integration is None or integration.kind != "servicenow" or integration.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown webhook")

    body = await request.body()
    if len(body) > WEBHOOK_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")
    timestamp = request.headers.get("X-NexusAI-Timestamp", "")
    signature = request.headers.get("X-NexusAI-Signature", "")
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        raise _REJECTED from None
    if age > WEBHOOK_TOLERANCE_SECONDS or not integration.webhook_secret_encrypted:
        raise _REJECTED
    secret = runtime.box.decrypt(integration.webhook_secret_encrypted)["secret"]
    if not hmac.compare_digest(signature, expected_signature(secret, timestamp, body)):
        logger.warning("webhook signature mismatch", extra={"integration_id": str(integration_id)})
        raise _REJECTED

    try:
        event = json.loads(body)
        event_id = str(event["event_id"])[:100]
        event_type = str(event["event_type"])[:64]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed event") from None

    inserted = await session.scalar(
        insert(WebhookEvent)
        .values(
            id=uuid.uuid4(),
            organization_id=integration.organization_id,
            integration_id=integration.id,
            event_id=event_id,
            event_type=event_type,
            payload=event,
        )
        .on_conflict_do_nothing(constraint="uq_webhook_events_integration_event")
        .returning(WebhookEvent.id)
    )
    if inserted is None:
        return {"status": "duplicate"}  # already processed: acknowledge, change nothing

    updated = None
    if event_type in ("incident.resolved", "incident.updated"):
        incident = event.get("incident") or {}
        ticket = await session.scalar(
            select(Ticket).where(
                Ticket.organization_id == integration.organization_id,
                Ticket.external_system == "servicenow",
                Ticket.external_id == str(incident.get("sys_id", "")),
            )
        )
        if ticket is not None and incident.get("state"):
            ticket.status = str(incident["state"])[:16]
            updated = ticket.id
    audit.record(
        session,
        action="integration.webhook_received",
        organization_id=integration.organization_id,
        actor_id=None,
        request=request,
        target_type="integration",
        target_id=integration.id,
        details={
            "event_type": event_type,
            "event_id": event_id,
            "ticket_id": str(updated) if updated else None,
        },
    )
    await session.commit()
    return {"status": "processed"}
