"""Agent runs and approvals: the rules around the graphs.

Approval rules:
- Only managers and org admins can approve or reject.
- Separation of duties: nobody can decide on their own request, whatever their role.
- A request expires after `approval_ttl_hours`; expired requests can't be approved.
- The decision is a single conditional UPDATE (... WHERE status = 'pending'), so two
  people clicking "approve" at the same moment can't both trigger execution.
- The decision is committed before execution starts. Execution is idempotent per
  step, so a run that failed or was interrupted can safely be retried.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.embeddings import EmbeddingProvider
from app.modules.agents.graph import EXECUTE_GRAPH, PLAN_GRAPH, Deps, StepStatus
from app.modules.agents.models import AgentRun, ApprovalRequest, ApprovalStatus, RunStatus
from app.modules.agents.planner import Planner
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.auth.roles import Role
from app.modules.integrations.runtime import IntegrationRuntime

APPROVER_ROLES = {Role.ORG_ADMIN.value, Role.MANAGER.value}


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class Conflict(Exception):
    pass


def can_see_all_runs(user: User) -> bool:
    return user.role in APPROVER_ROLES


async def get_run(session: AsyncSession, run_id: uuid.UUID, user: User) -> AgentRun:
    query = select(AgentRun).where(
        AgentRun.id == run_id, AgentRun.organization_id == user.organization_id
    )
    if not can_see_all_runs(user):
        query = query.where(AgentRun.requested_by == user.id)
    run = await session.scalar(query)
    if run is None:
        raise NotFound
    return run


async def start_run(
    session: AsyncSession,
    *,
    user: User,
    request_text: str,
    planner: Planner,
    embedder: EmbeddingProvider,
    settings: Settings,
    http_request: Request | None,
    integrations: IntegrationRuntime | None = None,
) -> AgentRun:
    run_id = uuid.uuid4()
    hub = integrations.for_org(session, user.organization_id) if integrations else None
    deps = Deps(session, user, settings, embedder, run_id, planner, hub)
    state = await PLAN_GRAPH.ainvoke(
        {"request": request_text.strip()}, config={"configurable": {"deps": deps}}
    )
    now = datetime.now(UTC)
    run = AgentRun(
        id=run_id,
        organization_id=user.organization_id,
        requested_by=user.id,
        request=request_text.strip(),
        status=state["status"],
        planner=planner.name,
        steps=state.get("steps", []),
        summary=state.get("summary"),
        error=state.get("error"),
        completed_at=None if state["status"] == RunStatus.AWAITING_APPROVAL else now,
    )
    session.add(run)
    await session.flush()
    audit.record(
        session,
        action="agent.run_started",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=http_request,
        target_type="agent_run",
        target_id=run.id,
        details={"status": run.status, "tools": [s["tool"] for s in run.steps]},
    )
    if run.status == RunStatus.AWAITING_APPROVAL:
        approval = ApprovalRequest(
            organization_id=user.organization_id,
            agent_run_id=run.id,
            requested_by=user.id,
            status=ApprovalStatus.PENDING,
            actions=[
                {k: s[k] for k in ("index", "tool", "args", "reason")}
                for s in run.steps
                if s["mutating"]
            ],
            expires_at=now + timedelta(hours=settings.approval_ttl_hours),
        )
        session.add(approval)
        await session.flush()
        audit.record(
            session,
            action="agent.approval_requested",
            organization_id=user.organization_id,
            actor_id=user.id,
            request=http_request,
            target_type="approval_request",
            target_id=approval.id,
            details={"run_id": str(run.id), "actions": [a["tool"] for a in approval.actions]},
        )
    await session.commit()
    await session.refresh(run)
    return run


async def expire_overdue(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Mark pending approvals past their deadline (and their runs) as expired."""
    expired = (
        await session.scalars(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.organization_id == organization_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at <= datetime.now(UTC),
            )
            .values(status=ApprovalStatus.EXPIRED)
            .returning(ApprovalRequest.agent_run_id)
        )
    ).all()
    if expired:
        runs = await session.scalars(select(AgentRun).where(AgentRun.id.in_(expired)))
        for run in runs:
            run.status = RunStatus.EXPIRED
            run.completed_at = datetime.now(UTC)
            run.steps = [
                {**s, "status": StepStatus.SKIPPED}
                if s["status"] == StepStatus.AWAITING_APPROVAL
                else s
                for s in run.steps
            ]
        await session.commit()


async def get_approval(
    session: AsyncSession, approval_id: uuid.UUID, user: User
) -> ApprovalRequest:
    approval = await session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.organization_id == user.organization_id,
        )
    )
    if approval is None:
        raise NotFound
    return approval


async def decide(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    user: User,
    approve: bool,
    comment: str | None,
    embedder: EmbeddingProvider,
    settings: Settings,
    http_request: Request | None,
    integrations: IntegrationRuntime | None = None,
) -> AgentRun:
    await expire_overdue(session, user.organization_id)
    approval = await get_approval(session, approval_id, user)
    if approval.requested_by == user.id:
        raise Forbidden(
            "You can't approve or reject your own request; another manager or admin must."
        )

    now = datetime.now(UTC)
    decided = await session.scalar(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval.id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.expires_at > now,
        )
        .values(
            status=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
            decided_by=user.id,
            decided_at=now,
            comment=(comment or "").strip() or None,
        )
        .returning(ApprovalRequest.id)
    )
    if decided is None:
        await session.rollback()
        await session.refresh(approval)
        raise Conflict(f"This request is already {approval.status}.")

    run = await session.get(AgentRun, approval.agent_run_id)
    assert run is not None
    audit.record(
        session,
        action="agent.approved" if approve else "agent.rejected",
        organization_id=user.organization_id,
        actor_id=user.id,
        request=http_request,
        target_type="approval_request",
        target_id=approval.id,
        details={"run_id": str(run.id), "comment": (comment or "").strip() or None},
    )
    if not approve:
        run.status = RunStatus.REJECTED
        run.completed_at = now
        run.steps = [
            {**s, "status": StepStatus.REJECTED}
            if s["status"] == StepStatus.AWAITING_APPROVAL
            else s
            for s in run.steps
        ]
        run.summary = "Rejected" + (f": {comment.strip()}" if comment and comment.strip() else ".")
        await session.commit()
        await session.refresh(run)
        return run

    run.status = RunStatus.EXECUTING
    await session.commit()  # the decision is durable before anything executes
    return await execute(
        session,
        run=run,
        actor=user,
        embedder=embedder,
        settings=settings,
        http_request=http_request,
        integrations=integrations,
    )


async def execute(
    session: AsyncSession,
    *,
    run: AgentRun,
    actor: User,
    embedder: EmbeddingProvider,
    settings: Settings,
    http_request: Request | None,
    integrations: IntegrationRuntime | None = None,
) -> AgentRun:
    # Records are created on behalf of the requester; the approver is in the audit log.
    requester = await session.get(User, run.requested_by) if run.requested_by else None
    hub = integrations.for_org(session, run.organization_id) if integrations else None
    deps = Deps(session, requester or actor, settings, embedder, run.id, integrations=hub)
    state = await EXECUTE_GRAPH.ainvoke(
        {"request": run.request, "steps": run.steps}, config={"configurable": {"deps": deps}}
    )
    run.steps = state["steps"]
    run.status = state["status"]
    run.summary = state["summary"]
    run.error = state.get("error")
    run.completed_at = datetime.now(UTC)
    for step in run.steps:
        if step["mutating"] and step["status"] in (StepStatus.DONE, StepStatus.FAILED):
            audit.record(
                session,
                action="agent.action_executed"
                if step["status"] == StepStatus.DONE
                else "agent.action_failed",
                organization_id=run.organization_id,
                actor_id=actor.id,
                request=http_request,
                target_type="agent_run",
                target_id=run.id,
                details={
                    "step": step["index"],
                    "tool": step["tool"],
                    "result": step.get("result"),
                    "error": step.get("error"),
                },
            )
    await session.commit()
    await session.refresh(run)
    return run


async def retry(
    session: AsyncSession,
    *,
    run: AgentRun,
    user: User,
    embedder: EmbeddingProvider,
    settings: Settings,
    http_request: Request | None,
    integrations: IntegrationRuntime | None = None,
) -> AgentRun:
    approval = await session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.agent_run_id == run.id)
    )
    if approval is None or approval.status != ApprovalStatus.APPROVED:
        raise Conflict("Only approved runs can be retried.")
    if run.status not in (RunStatus.FAILED, RunStatus.EXECUTING):
        raise Conflict(f"This run is {run.status}; there is nothing to retry.")
    run.steps = [
        {**s, "status": StepStatus.AWAITING_APPROVAL, "error": None}
        if s["mutating"]
        and s["status"] in (StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.AWAITING_APPROVAL)
        else s
        for s in run.steps
    ]
    run.status = RunStatus.EXECUTING
    await session.commit()
    return await execute(
        session,
        run=run,
        actor=user,
        embedder=embedder,
        settings=settings,
        http_request=http_request,
        integrations=integrations,
    )


def visible_runs_query(user: User, mine: bool):
    query = select(AgentRun).where(AgentRun.organization_id == user.organization_id)
    if mine or not can_see_all_runs(user):
        query = query.where(AgentRun.requested_by == user.id)
    return query
