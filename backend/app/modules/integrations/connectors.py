"""Connector framework: one place that decides how NexusAI talks to other systems.

Every outbound call goes through `HttpConnector._call`, which provides:
- a host allowlist check (a configured base URL can't point the server at
  arbitrary internal addresses: SSRF protection),
- timeouts,
- retries with exponential backoff and jitter for timeouts, connection errors,
  429 and 502/503/504, honouring the server's Retry-After (capped),
- no retries for other 4xx errors (bad credentials, bad input won't get better),
- a call log row per logical call (operation, outcome, status, attempts, latency),
  written in its own transaction so it survives a rollback of the caller's work.

Creates are only retried when they carry an Idempotency-Key, so a retry after a
timeout can't create a second incident or send a second email.
"""

import asyncio
import hashlib
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings

logger = logging.getLogger("nexusai.integrations")

RETRYABLE_STATUS = {429, 502, 503, 504}


class ConnectorError(Exception):
    """A call to an external system failed. The message is safe to show to users."""

    def __init__(self, message: str, *, retryable: bool, http_status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.http_status = http_status


def check_base_url(url: str, allowed_hosts: list[str]) -> str:
    """Validate a configured base URL. Raises ValueError with a user-safe message."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The URL must start with http:// or https:// and include a host")
    if parts.username or parts.password:
        raise ValueError("Put credentials in the credential fields, not in the URL")
    host = parts.hostname.lower()
    allowed = any(
        host == entry or (entry.startswith(".") and host.endswith(entry)) for entry in allowed_hosts
    )
    if not allowed:
        raise ValueError(
            f"Host '{host}' is not allowed. Ask an administrator to add it to "
            "NEXUS_INTEGRATION_ALLOWED_HOSTS."
        )
    return url.strip().rstrip("/")


@dataclass(frozen=True)
class CallRecord:
    organization_id: uuid.UUID
    integration_id: uuid.UUID
    operation: str
    outcome: str
    http_status: int | None
    attempts: int
    latency_ms: int
    error: str | None


Recorder = Callable[[CallRecord], Awaitable[None]]


class HttpConnector:
    kind = "base"

    def __init__(
        self,
        *,
        organization_id: uuid.UUID,
        integration_id: uuid.UUID,
        base_url: str,
        config: dict[str, Any],
        secrets: dict[str, Any],
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.integration_id = integration_id
        self.base_url = check_base_url(base_url, settings.integration_allowed_hosts)
        self.config = config
        self.secrets = secrets
        self.settings = settings
        self.transport = transport
        self.recorder = recorder

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.settings.integration_timeout_seconds,
            transport=self.transport,
            follow_redirects=False,  # a redirect could lead outside the allowlist
        )

    def _delay(self, attempt: int, response: httpx.Response | None) -> float:
        cap = self.settings.integration_max_retry_after_seconds
        if response is not None and (retry_after := response.headers.get("Retry-After")):
            try:
                return min(float(retry_after), cap)
            except ValueError:
                pass
        base = self.settings.integration_retry_base_seconds * (2 ** (attempt - 1))
        return min(base * (0.5 + random.random() / 2), cap)  # jitter spreads out retries

    async def _call(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        retry: bool,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        max_attempts = self.settings.integration_max_attempts if retry else 1
        started = time.perf_counter()
        attempts = 0
        status: int | None = None
        error = "unknown error"
        retryable = True
        async with self._client() as client:
            while attempts < max_attempts:
                attempts += 1
                response: httpx.Response | None = None
                try:
                    response = await client.request(
                        method, path, headers={**self._auth_headers(), **(headers or {})}, **kwargs
                    )
                    status = response.status_code
                except httpx.TimeoutException:
                    error = "timed out"
                except httpx.TransportError as exc:
                    error = f"connection failed ({type(exc).__name__})"
                else:
                    if status < 400:
                        await self._record(operation, "success", status, attempts, started, None)
                        return response
                    if status not in RETRYABLE_STATUS:
                        retryable = False
                        error = f"rejected the request (HTTP {status})"
                        if status == 401 and await self._on_unauthorized():
                            continue  # e.g. refreshed an expired OAuth token; try once more
                        break
                    error = f"returned HTTP {status}"
                if attempts < max_attempts:
                    await asyncio.sleep(self._delay(attempts, response))

        await self._record(operation, "failure", status, attempts, started, error)
        raise ConnectorError(
            f"{self.kind} {operation} failed: {error}", retryable=retryable, http_status=status
        )

    async def _record(
        self,
        operation: str,
        outcome: str,
        status: int | None,
        attempts: int,
        started: float,
        error: str | None,
    ) -> None:
        if self.recorder is None:
            return
        record = CallRecord(
            organization_id=self.organization_id,
            integration_id=self.integration_id,
            operation=operation,
            outcome=outcome,
            http_status=status,
            attempts=attempts,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
        )
        try:
            await self.recorder(record)
        except Exception:  # the log must never break the actual work
            logger.exception("failed to record integration call")

    def _auth_headers(self) -> dict[str, str]:
        return {}

    async def _on_unauthorized(self) -> bool:
        return False

    async def health(self) -> None:
        raise NotImplementedError


class ServiceNowConnector(HttpConnector):
    kind = "servicenow"
    URGENCY = {"high": "1", "medium": "2", "low": "3"}

    def _auth_headers(self) -> dict[str, str]:
        auth = httpx.BasicAuth(self.secrets["username"], self.secrets["password"])
        request = next(auth.auth_flow(httpx.Request("GET", self.base_url)))
        return {"Authorization": request.headers["Authorization"]}

    async def health(self) -> None:
        await self._call(
            "health_check",
            "GET",
            "/api/now/table/sys_user",
            retry=True,
            params={"sysparm_limit": 1},
        )

    async def create_incident(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        caller_email: str | None,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        response = await self._call(
            "create_incident",
            "POST",
            "/api/now/table/incident",
            retry=True,  # safe: the idempotency key makes a repeated create return the same record
            headers={"Idempotency-Key": idempotency_key},
            json={
                "short_description": title,
                "description": description,
                "urgency": self.URGENCY.get(priority, "2"),
                "caller_email": caller_email,
                "correlation_id": correlation_id,
            },
        )
        result = response.json()["result"]
        return {
            "sys_id": result["sys_id"],
            "number": result["number"],
            "url": f"{self.base_url}/nav_to.do?uri=incident.do?sys_id={result['sys_id']}",
        }

    async def register_webhook(self, url: str, secret: str) -> None:
        await self._call(
            "register_webhook",
            "POST",
            "/api/x_nexus/webhooks",
            retry=True,
            json={"url": url, "secret": secret},
        )


# Salesforce OAuth tokens, cached per integration and credential, shared across requests.
_token_cache: dict[str, tuple[str, float]] = {}


class SalesforceConnector(HttpConnector):
    kind = "salesforce"

    def _cache_key(self) -> str:
        digest = hashlib.sha256(
            f"{self.secrets['client_id']}:{self.secrets['client_secret']}".encode()
        ).hexdigest()
        return f"{self.integration_id}:{digest}"

    async def _token(self, force: bool = False) -> str:
        cached = _token_cache.get(self._cache_key())
        if cached and not force and cached[1] > time.time() + 30:
            return cached[0]
        response = await self._call(
            "oauth_token",
            "POST",
            "/services/oauth2/token",
            retry=True,
            data={
                "grant_type": "client_credentials",
                "client_id": self.secrets["client_id"],
                "client_secret": self.secrets["client_secret"],
            },
        )
        body = response.json()
        _token_cache[self._cache_key()] = (
            body["access_token"],
            time.time() + float(body.get("expires_in", 900)),
        )
        return body["access_token"]

    def _auth_headers(self) -> dict[str, str]:
        cached = _token_cache.get(self._cache_key())
        return {"Authorization": f"Bearer {cached[0]}"} if cached else {}

    async def _on_unauthorized(self) -> bool:
        # An expired or revoked token: get a fresh one once, then retry the call.
        if getattr(self, "_refreshed", False):
            return False
        self._refreshed = True
        await self._token(force=True)
        return True

    async def health(self) -> None:
        await self._token(force=True)

    async def find_contact(self, email: str) -> dict[str, Any] | None:
        await self._token()
        response = await self._call(
            "find_contact",
            "GET",
            "/services/data/v60.0/contacts",
            retry=True,
            params={"email": email},
        )
        records = response.json().get("records", [])
        if not records:
            return None
        record = records[0]
        return {
            "name": record.get("Name"),
            "account": record.get("Account"),
            "tier": record.get("Tier"),
            "owner": record.get("Owner"),
        }


class EmailConnector(HttpConnector):
    """Microsoft-Graph-style sendMail."""

    kind = "email"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secrets['api_key']}"}

    @property
    def sender(self) -> str:
        return self.config["sender"]

    async def health(self) -> None:
        await self._call("health_check", "GET", f"/v1.0/users/{quote(self.sender)}", retry=True)

    async def send(self, *, to: str, subject: str, body: str, idempotency_key: str) -> str | None:
        response = await self._call(
            "send_mail",
            "POST",
            f"/v1.0/users/{quote(self.sender)}/sendMail",
            retry=True,  # the idempotency key prevents duplicate emails on retry
            headers={"Idempotency-Key": idempotency_key},
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": False,
            },
        )
        return response.headers.get("X-Message-Id")


CONNECTORS: dict[str, type[HttpConnector]] = {
    "servicenow": ServiceNowConnector,
    "salesforce": SalesforceConnector,
    "email": EmailConnector,
}
