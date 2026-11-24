"""The tools agents may use. This registry is the complete list: no free-form SQL,
no shell, no HTTP to arbitrary URLs.

Each tool declares:
- an argument schema (validated before anything runs, at planning time AND again
  right before execution),
- whether it changes anything (`mutating`). Mutating tools never run without a
  human approval; read-only tools run immediately with the requester's permissions.

Mutating tools are idempotent per (run, step): running a step twice returns the
record created the first time instead of creating a second one.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.embeddings import EmbeddingProvider
from app.modules.agents.models import OutboxMessage, Ticket
from app.modules.auth.models import Organization, User
from app.modules.rag.retrieval import retrieve

TICKET_NUMBER_START = 1001
PLACEHOLDER = re.compile(r"\{\{\s*ticket_number\s*\}\}")


@dataclass
class ToolContext:
    session: AsyncSession
    user: User  # whose permissions read-only tools use (the requester)
    settings: Settings
    embedder: EmbeddingProvider
    run_id: uuid.UUID
    step_index: int
    # Values produced by earlier steps of the same run, e.g. {"ticket_number": "TCK-1001"}.
    outputs: dict[str, str]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchDocumentsArgs(_Args):
    query: str = Field(min_length=2, max_length=500)


class CreateTicketArgs(_Args):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    priority: Literal["low", "medium", "high"] = "medium"
    customer_email: EmailStr | None = None


class NotifyCustomerArgs(_Args):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[_Args]
    mutating: bool
    run: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "mutating": self.mutating,
            "requires_approval": self.mutating,
            "parameters": self.args_model.model_json_schema(),
        }


def fill_placeholders(text: str, outputs: dict[str, str]) -> str:
    """Replace {{ticket_number}} with the ticket created earlier in the same run."""
    if "ticket_number" not in outputs:
        return text
    return PLACEHOLDER.sub(outputs["ticket_number"], text)


async def _search_documents(ctx: ToolContext, args: SearchDocumentsArgs) -> dict[str, Any]:
    result = await retrieve(
        ctx.session,
        user=ctx.user,
        query=args.query,
        embedder=ctx.embedder,
        settings=ctx.settings,
        top_k=3,
    )
    return {
        "passages": [
            {
                "document_id": str(p.document_id),
                "document_title": p.document_title,
                "chunk_index": p.chunk_index,
                "excerpt": p.content[:400],
            }
            for p in result.passages
        ]
    }


async def _create_ticket(ctx: ToolContext, args: CreateTicketArgs) -> dict[str, Any]:
    existing = await ctx.session.scalar(
        select(Ticket).where(Ticket.agent_run_id == ctx.run_id, Ticket.step_index == ctx.step_index)
    )
    if existing is None:
        # Lock the organization row so concurrent runs get consecutive, unique numbers.
        await ctx.session.execute(
            select(Organization.id)
            .where(Organization.id == ctx.user.organization_id)
            .with_for_update()
        )
        last = await ctx.session.scalar(
            select(func.max(Ticket.number)).where(
                Ticket.organization_id == ctx.user.organization_id
            )
        )
        existing = Ticket(
            organization_id=ctx.user.organization_id,
            number=(last or TICKET_NUMBER_START - 1) + 1,
            title=fill_placeholders(args.title, ctx.outputs),
            description=fill_placeholders(args.description, ctx.outputs),
            priority=args.priority,
            status="open",
            customer_email=args.customer_email,
            created_by=ctx.user.id,
            agent_run_id=ctx.run_id,
            step_index=ctx.step_index,
        )
        ctx.session.add(existing)
        await ctx.session.flush()
    number = f"TCK-{existing.number}"
    ctx.outputs["ticket_number"] = number
    return {"ticket_id": str(existing.id), "ticket_number": number, "priority": existing.priority}


async def _notify_customer(ctx: ToolContext, args: NotifyCustomerArgs) -> dict[str, Any]:
    existing = await ctx.session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.agent_run_id == ctx.run_id, OutboxMessage.step_index == ctx.step_index
        )
    )
    if existing is None:
        existing = OutboxMessage(
            organization_id=ctx.user.organization_id,
            recipient=str(args.to),
            subject=fill_placeholders(args.subject, ctx.outputs),
            body=fill_placeholders(args.body, ctx.outputs),
            status="recorded",
            agent_run_id=ctx.run_id,
            step_index=ctx.step_index,
        )
        ctx.session.add(existing)
        await ctx.session.flush()
    return {
        "message_id": str(existing.id),
        "to": existing.recipient,
        "status": "recorded",
        "note": "Recorded in the outbox; not sent (no email connector yet).",
    }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="search_documents",
            description="Search the organization's documents the requester can access.",
            args_model=SearchDocumentsArgs,
            mutating=False,
            run=_search_documents,
        ),
        Tool(
            name="create_ticket",
            description="Create a support ticket in the internal ticketing system.",
            args_model=CreateTicketArgs,
            mutating=True,
            run=_create_ticket,
        ),
        Tool(
            name="notify_customer",
            description=(
                "Email a customer. Use {{ticket_number}} in the subject or body to insert "
                "the number of a ticket created earlier in the same run."
            ),
            args_model=NotifyCustomerArgs,
            mutating=True,
            run=_notify_customer,
        ),
    )
}
