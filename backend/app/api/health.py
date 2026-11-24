"""Health and system-info endpoints.

- /health/live  : is the process running? Never touches dependencies.
- /health/ready : can the service handle traffic? Checks PostgreSQL with a timeout.
                  Redis is reported too, but it is not critical: rate limiting fails
                  open, so a Redis outage doesn't take the API down.
- /system/info  : non-sensitive build/runtime info for the dashboard.
"""

import asyncio
import logging
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app import __version__
from app.api.deps import get_database, get_rate_limiter, get_settings_dep
from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.db.session import Database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

CheckStatus = Literal["ok", "error"]
CheckState = Literal["ok", "error", "disabled"]


class LiveResponse(BaseModel):
    status: Literal["ok"]


class CheckResult(BaseModel):
    status: CheckState
    critical: bool = True
    latency_ms: float | None = None
    error: str | None = None


class ReadyResponse(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckResult]


class SystemInfo(BaseModel):
    name: str
    version: str
    environment: str
    # Lets the UI say how answers are produced (e.g. "offline, quotes your documents").
    answer_provider: str
    chat_model: str | None
    embedding_provider: str


@router.get("/health/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "A dependency is unavailable"}},
)
async def ready(
    response: Response,
    db: Annotated[Database, Depends(get_database)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> ReadyResponse:
    start = time.perf_counter()
    try:
        await db.ping()
        database = CheckResult(
            status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2)
        )
    except Exception as exc:  # any failure means "not ready"; details go to logs only
        logger.warning("database readiness check failed", extra={"error": type(exc).__name__})
        # Expose only the error type: connection strings or hostnames must not leak.
        database = CheckResult(status="error", error=type(exc).__name__)

    redis = await _check_redis(limiter)
    overall: CheckStatus = "ok" if database.status == "ok" else "error"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status=overall, checks={"database": database, "redis": redis})


async def _check_redis(limiter: RateLimiter) -> CheckResult:
    if not limiter.enabled:
        return CheckResult(status="disabled", critical=False)
    start = time.perf_counter()
    try:
        await asyncio.wait_for(limiter.ping(), timeout=1.0)
    except Exception as exc:
        logger.warning("redis readiness check failed", extra={"error": type(exc).__name__})
        return CheckResult(status="error", critical=False, error=type(exc).__name__)
    return CheckResult(
        status="ok", critical=False, latency_ms=round((time.perf_counter() - start) * 1000, 2)
    )


@router.get("/system/info", response_model=SystemInfo)
async def system_info(settings: Annotated[Settings, Depends(get_settings_dep)]) -> SystemInfo:
    return SystemInfo(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        answer_provider=settings.answer_provider,
        chat_model=settings.chat_model if settings.answer_provider == "openai" else None,
        embedding_provider=settings.embedding_provider,
    )
