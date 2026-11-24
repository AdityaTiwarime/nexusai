"""Agent workflow and approval endpoints.

- Any organization member can start a run and see their own runs.
- Managers and org admins see every run in the organization and decide on approvals
  (never their own).
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_rate_limiter, get_session, get_settings_dep
from app.core.config import Settings
from app.core.rate_limit import Limit, RateLimiter
from app.llm.chat import ChatError
from app.llm.embeddings import EmbeddingError, EmbeddingProvider
from app.modules.agents import service
from app.modules.agents.models import AgentRun, ApprovalRequest, OutboxMessage, Ticket
from app.modules.agents.planner import Planner
from app.modules.agents.schemas import (
    ApprovalBrief,
    ApprovalOut,
    Decision,
    OutboxOut,
    RunCreate,
    RunOut,
    TicketOut,
)
from app.modules.agents.tools import TOOLS
from app.modules.auth.deps import require_org_member, require_roles
from app.modules.auth.models import User
from app.modules.auth.roles import Role
from app.modules.rag.router import get_embedder

router = APIRouter(tags=["agents"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
Member = Annotated[User, Depends(require_org_member())]
Approver = Annotated[User, Depends(require_roles(Role.ORG_ADMIN, Role.MANAGER))]
Embedder = Annotated[EmbeddingProvider, Depends(get_embedder)]
Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_planner(request: Request) -> Planner:
    return request.app.state.planner


PlannerDep = Annotated[Planner, Depends(get_planner)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, service.Forbidden):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, ChatError | EmbeddingError):
        if exc.retryable:
            return HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The AI service is temporarily unavailable. Please try again.",
            )
        return HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI service rejected the request.")
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


async def _emails(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.email).where(User.id.in_(wanted)))
    return dict(rows.all())


async def _runs_out(session: AsyncSession, runs: list[AgentRun]) -> list[RunOut]:
    approvals = {
        a.agent_run_id: a
        for a in await session.scalars(
            select(ApprovalRequest).where(ApprovalRequest.agent_run_id.in_([r.id for r in runs]))
        )
    }
    emails = await _emails(
        session,
        {r.requested_by for r in runs} | {a.decided_by for a in approvals.values()},
    )
    out = []
    for run in runs:
        item = RunOut.model_validate(run)
        item.requested_by_email = emails.get(run.requested_by) if run.requested_by else None
        if approval := approvals.get(run.id):
            item.approval = ApprovalBrief(
                id=approval.id,
                status=approval.status,
                expires_at=approval.expires_at,
                decided_by_email=emails.get(approval.decided_by) if approval.decided_by else None,
                decided_at=approval.decided_at,
                comment=approval.comment,
            )
        out.append(item)
    return out


async def _approvals_out(
    session: AsyncSession, approvals: list[ApprovalRequest]
) -> list[ApprovalOut]:
    runs = {
        r.id: r
        for r in await session.scalars(
            select(AgentRun).where(AgentRun.id.in_([a.agent_run_id for a in approvals]))
        )
    }
    emails = await _emails(
        session, {a.requested_by for a in approvals} | {a.decided_by for a in approvals}
    )
    return [
        ApprovalOut(
            id=a.id,
            agent_run_id=a.agent_run_id,
            status=a.status,
            request=runs[a.agent_run_id].request,
            actions=a.actions,
            requested_by_email=emails.get(a.requested_by) if a.requested_by else None,
            expires_at=a.expires_at,
            decided_by_email=emails.get(a.decided_by) if a.decided_by else None,
            decided_at=a.decided_at,
            comment=a.comment,
            created_at=a.created_at,
        )
        for a in approvals
    ]


@router.get("/agents/tools")
async def list_tools(caller: Member) -> list[dict[str, Any]]:
    return [tool.public() for tool in TOOLS.values()]


@router.post("/agents/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def start_run(
    body: RunCreate,
    request: Request,
    caller: Member,
    session: Session,
    settings: SettingsDep,
    planner: PlannerDep,
    embedder: Embedder,
    limiter: Limiter,
) -> RunOut:
    decision = await limiter.hit(
        Limit("agent_run", settings.agent_rate_limit_per_user, 60), str(caller.id)
    )
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Please wait a moment.",
            headers={"Retry-After": str(decision.retry_after)},
        )
    try:
        run = await service.start_run(
            session,
            user=caller,
            request_text=body.request,
            planner=planner,
            embedder=embedder,
            settings=settings,
            http_request=request,
            integrations=request.app.state.integrations,
        )
    except (ChatError, EmbeddingError) as exc:
        await session.rollback()
        raise _http(exc) from None
    return (await _runs_out(session, [run]))[0]


@router.get("/agents/runs", response_model=list[RunOut])
async def list_runs(
    caller: Member,
    session: Session,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[RunOut]:
    await service.expire_overdue(session, caller.organization_id)
    runs = list(
        await session.scalars(
            service.visible_runs_query(caller, mine)
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .limit(limit)
        )
    )
    return await _runs_out(session, runs)


@router.get("/agents/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, caller: Member, session: Session) -> RunOut:
    await service.expire_overdue(session, caller.organization_id)
    try:
        run = await service.get_run(session, run_id, caller)
    except service.NotFound as exc:
        raise _http(exc) from None
    return (await _runs_out(session, [run]))[0]


@router.post("/agents/runs/{run_id}/retry", response_model=RunOut)
async def retry_run(
    run_id: uuid.UUID,
    request: Request,
    caller: Approver,
    session: Session,
    settings: SettingsDep,
    embedder: Embedder,
) -> RunOut:
    try:
        run = await service.get_run(session, run_id, caller)
        run = await service.retry(
            session,
            run=run,
            user=caller,
            embedder=embedder,
            settings=settings,
            http_request=request,
            integrations=request.app.state.integrations,
        )
    except (service.NotFound, service.Conflict) as exc:
        raise _http(exc) from None
    return (await _runs_out(session, [run]))[0]


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    caller: Approver,
    session: Session,
    status_filter: Annotated[
        Literal["pending", "approved", "rejected", "expired"] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ApprovalOut]:
    await service.expire_overdue(session, caller.organization_id)
    query = select(ApprovalRequest).where(ApprovalRequest.organization_id == caller.organization_id)
    if status_filter:
        query = query.where(ApprovalRequest.status == status_filter)
    approvals = list(
        await session.scalars(
            query.order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id).limit(limit)
        )
    )
    return await _approvals_out(session, approvals)


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
async def get_approval(approval_id: uuid.UUID, caller: Approver, session: Session) -> ApprovalOut:
    await service.expire_overdue(session, caller.organization_id)
    try:
        approval = await service.get_approval(session, approval_id, caller)
    except service.NotFound as exc:
        raise _http(exc) from None
    return (await _approvals_out(session, [approval]))[0]


async def _decide(
    approve: bool,
    approval_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: User,
    session: AsyncSession,
    settings: Settings,
    embedder: EmbeddingProvider,
) -> RunOut:
    try:
        run = await service.decide(
            session,
            approval_id=approval_id,
            user=caller,
            approve=approve,
            comment=body.comment,
            embedder=embedder,
            settings=settings,
            http_request=request,
            integrations=request.app.state.integrations,
        )
    except (service.NotFound, service.Forbidden, service.Conflict) as exc:
        raise _http(exc) from None
    return (await _runs_out(session, [run]))[0]


@router.post("/approvals/{approval_id}/approve", response_model=RunOut)
async def approve(
    approval_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: Approver,
    session: Session,
    settings: SettingsDep,
    embedder: Embedder,
) -> RunOut:
    return await _decide(True, approval_id, body, request, caller, session, settings, embedder)


@router.post("/approvals/{approval_id}/reject", response_model=RunOut)
async def reject(
    approval_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: Approver,
    session: Session,
    settings: SettingsDep,
    embedder: Embedder,
) -> RunOut:
    return await _decide(False, approval_id, body, request, caller, session, settings, embedder)


@router.get("/tickets", response_model=list[TicketOut])
async def list_tickets(caller: Member, session: Session) -> list[Ticket]:
    rows = await session.scalars(
        select(Ticket)
        .where(Ticket.organization_id == caller.organization_id)
        .order_by(Ticket.number.desc())
        .limit(100)
    )
    return list(rows)


@router.get("/outbox", response_model=list[OutboxOut])
async def list_outbox(caller: Approver, session: Session) -> list[OutboxMessage]:
    rows = await session.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.organization_id == caller.organization_id)
        .order_by(OutboxMessage.created_at.desc())
        .limit(100)
    )
    return list(rows)
