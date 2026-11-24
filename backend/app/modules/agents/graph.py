"""Agent workflows as LangGraph state machines.

Two graphs, with a human in between:

    plan graph:     plan -> run_read_only -> (request_approval | finish)
                      \\-> (planning failed) -> END
    ... a manager approves or rejects (hours later, in another HTTP request) ...
    execute graph:  execute_approved -> verify -> finish

The pause for approval is stored in PostgreSQL (agent_runs + approval_requests),
not in a LangGraph checkpointer: the approval has to survive restarts, be
tenant-scoped and audited like every other record, and the async SQLAlchemy
stack has no LangGraph checkpointer, so a second database driver would be
needed. Each graph runs to completion inside one request.

Nodes get their dependencies (database session, planner, tool context) through
LangGraph's `config["configurable"]`, so the graphs themselves hold no state.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.embeddings import EmbeddingProvider
from app.modules.agents.models import OutboxMessage, RunStatus, Ticket
from app.modules.agents.planner import Planner, PlanningError, validate_plan
from app.modules.agents.tools import TOOLS, ToolContext
from app.modules.auth.models import User

logger = logging.getLogger("nexusai.agents")


class StepStatus:
    PLANNED = "planned"
    DONE = "done"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class AgentState(TypedDict, total=False):
    request: str
    steps: list[dict[str, Any]]
    status: str
    summary: str | None
    error: str | None


@dataclass
class Deps:
    session: AsyncSession
    user: User  # the requester: read-only tools run with their permissions
    settings: Settings
    embedder: EmbeddingProvider
    run_id: uuid.UUID
    planner: Planner | None = None


def _deps(config: RunnableConfig) -> Deps:
    return config["configurable"]["deps"]


def _context(deps: Deps, index: int, outputs: dict[str, str]) -> ToolContext:
    return ToolContext(
        session=deps.session,
        user=deps.user,
        settings=deps.settings,
        embedder=deps.embedder,
        run_id=deps.run_id,
        step_index=index,
        outputs=outputs,
    )


async def _run_step(deps: Deps, step: dict[str, Any], outputs: dict[str, str]) -> None:
    """Run one tool inside a savepoint, so a failing step can't leave half its writes behind."""
    tool = TOOLS[step["tool"]]
    try:
        args = tool.args_model.model_validate(step["args"])  # validated again, right before running
        async with deps.session.begin_nested():
            step["result"] = await tool.run(_context(deps, step["index"], outputs), args)
        step["status"] = StepStatus.DONE
    except Exception as exc:
        logger.exception("agent step failed", extra={"run_id": str(deps.run_id), "tool": tool.name})
        step["status"] = StepStatus.FAILED
        step["error"] = f"{tool.name} failed: {type(exc).__name__}"


# --- plan graph ----------------------------------------------------------------------


async def plan_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    assert deps.planner is not None
    try:
        planned = validate_plan(
            await deps.planner.plan(state["request"]), deps.settings.agent_max_steps
        )
    except PlanningError as exc:
        return {"status": RunStatus.FAILED, "error": str(exc), "steps": []}
    steps = [
        {
            "index": i,
            "tool": p.tool,
            "args": p.args,
            "reason": p.reason,
            "mutating": TOOLS[p.tool].mutating,
            "status": StepStatus.PLANNED,
            "result": None,
            "error": None,
        }
        for i, p in enumerate(planned)
    ]
    return {"steps": steps}


async def run_read_only_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    steps = [dict(s) for s in state["steps"]]
    outputs: dict[str, str] = {}
    for step in steps:
        if step["mutating"]:
            step["status"] = StepStatus.AWAITING_APPROVAL
        else:
            await _run_step(deps, step, outputs)
    return {"steps": steps}


async def request_approval_node(state: AgentState, config: RunnableConfig) -> AgentState:
    return {
        "status": RunStatus.AWAITING_APPROVAL,
        "summary": summarize(state["steps"], pending=True),
    }


async def finish_node(state: AgentState, config: RunnableConfig) -> AgentState:
    failed = any(s["status"] == StepStatus.FAILED for s in state["steps"])
    return {
        "status": RunStatus.FAILED if failed else RunStatus.COMPLETED,
        "summary": summarize(state["steps"]),
        "error": next((s["error"] for s in state["steps"] if s.get("error")), None),
    }


def _after_plan(state: AgentState) -> str:
    return "failed" if state.get("status") == RunStatus.FAILED else "ok"


def _needs_approval(state: AgentState) -> str:
    return "approval" if any(s["mutating"] for s in state["steps"]) else "finish"


def build_plan_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("run_read_only", run_read_only_node)
    graph.add_node("request_approval", request_approval_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", _after_plan, {"failed": END, "ok": "run_read_only"})
    graph.add_conditional_edges(
        "run_read_only", _needs_approval, {"approval": "request_approval", "finish": "finish"}
    )
    graph.add_edge("request_approval", END)
    graph.add_edge("finish", END)
    return graph.compile()


# --- execute graph (after approval) ---------------------------------------------------


async def execute_node(state: AgentState, config: RunnableConfig) -> AgentState:
    deps = _deps(config)
    steps = [dict(s) for s in state["steps"]]
    # Outputs of steps that already ran (e.g. on a retry) feed {{ticket_number}}.
    outputs: dict[str, str] = {}
    for s in steps:
        if s["status"] == StepStatus.DONE and s["result"] and "ticket_number" in s["result"]:
            outputs["ticket_number"] = s["result"]["ticket_number"]
    failed = False
    for step in steps:
        if not step["mutating"] or step["status"] == StepStatus.DONE:
            continue
        if failed:
            step["status"] = StepStatus.SKIPPED
            continue
        await _run_step(deps, step, outputs)
        failed = step["status"] == StepStatus.FAILED
    return {"steps": steps}


async def verify_node(state: AgentState, config: RunnableConfig) -> AgentState:
    """Re-read every record the run claims to have created."""
    deps = _deps(config)
    steps = [dict(s) for s in state["steps"]]
    for step in steps:
        if step["status"] != StepStatus.DONE or not step["mutating"]:
            continue
        result = step["result"] or {}
        if step["tool"] == "create_ticket":
            found = await deps.session.scalar(
                select(Ticket.id).where(Ticket.id == uuid.UUID(result["ticket_id"]))
            )
        else:
            found = await deps.session.scalar(
                select(OutboxMessage.id).where(OutboxMessage.id == uuid.UUID(result["message_id"]))
            )
        result["verified"] = found is not None
        if not found:
            step["status"] = StepStatus.FAILED
            step["error"] = "The created record could not be found afterwards"
    return {"steps": steps}


def build_execute_graph():
    graph = StateGraph(AgentState)
    graph.add_node("execute_approved", execute_node)
    graph.add_node("verify", verify_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "execute_approved")
    graph.add_edge("execute_approved", "verify")
    graph.add_edge("verify", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


PLAN_GRAPH = build_plan_graph()
EXECUTE_GRAPH = build_execute_graph()


# --- summaries -------------------------------------------------------------------------


def summarize(steps: list[dict[str, Any]], pending: bool = False) -> str:
    parts = []
    for s in steps:
        result = s.get("result") or {}
        if s["status"] == StepStatus.DONE and s["tool"] == "search_documents":
            titles = sorted({p["document_title"] for p in result.get("passages", [])})
            parts.append(
                f"Found relevant passages in {', '.join(titles)}."
                if titles
                else "No relevant documents found."
            )
        elif s["status"] == StepStatus.DONE and s["tool"] == "create_ticket":
            parts.append(
                f"Created ticket {result['ticket_number']} ({result['priority']} priority)."
            )
        elif s["status"] == StepStatus.DONE and s["tool"] == "notify_customer":
            parts.append(f"Recorded an email to {result['to']} in the outbox (not sent).")
        elif s["status"] == StepStatus.FAILED:
            parts.append(f"Step {s['index'] + 1} failed: {s['error']}.")
    if pending:
        waiting = sum(1 for s in steps if s["status"] == StepStatus.AWAITING_APPROVAL)
        parts.append(f"{waiting} action{'s' if waiting != 1 else ''} waiting for approval.")
    return " ".join(parts) or "Nothing was done."
