"""Request ID, baseline security headers and access-log middleware.

Each request gets an ID (reused from the incoming X-Request-ID header when it is
a safe value, otherwise generated). The ID is stored in a context variable for
logging and returned in the response header so a user-reported error can be
matched to server logs.
"""

import json
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx

logger = logging.getLogger("nexusai.access")

REQUEST_ID_HEADER = "x-request-id"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _SAFE_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    """Pure ASGI middleware (avoids BaseHTTPMiddleware's streaming pitfalls)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        status_code = 500

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                # API responses carry tokens and tenant data: never cache them, and stop
                # browsers from guessing a different content type.
                names = {name.lower() for name, _ in headers}
                if b"cache-control" not in names:
                    headers.append((b"cache-control", b"no-store"))
                if b"x-content-type-options" not in names:
                    headers.append((b"x-content-type-options", b"nosniff"))
                message["headers"] = headers
            await send(message)

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send_with_header(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            # Handled here (not in a FastAPI exception handler) because Starlette's
            # ServerErrorMiddleware sits outside this middleware: by the time it runs,
            # the request ID context is gone. Here we can still return the ID.
            logger.exception("unhandled error", extra={"path": scope["path"]})
            if response_started:
                raise
            body = json.dumps({"detail": "Internal server error", "request_id": request_id})
            await send_with_header(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send_with_header({"type": "http.response.body", "body": body.encode()})
        finally:
            logger.info(
                "request completed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            request_id_ctx.reset(token)
