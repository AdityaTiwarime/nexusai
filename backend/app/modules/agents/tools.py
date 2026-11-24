"""The tools agents may use. This registry is the complete list: no free-form SQL,
no shell, no HTTP to arbitrary URLs. External systems are reached only through
the organization's configured connectors (app/modules/integrations).

Each tool declares:
- an argument schema (validated before anything runs, at planning time AND again
  right before execution),
- whether it changes anything (`mutating`). Mutating tools never run without a
  human approval; read-only tools run immediately with the requester's permissions.

Mutating tools are idempotent per (run, step): running a step twice returns the
record created the first time instead of creating a second one. Calls to external
systems carry an Idempotency-Key derived from (run, step) for the same reason.

Without a connection, tools fall back to internal records (ticket in NexusAI,
email recorded in the outbox but not sent) and say so in their result.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.embeddings import EmbeddingProvider
from app.modules.agents.models import OutboxMessage, Ticket
from app.modules.auth.models import User
from app.modules.integrations.runtime import IntegrationHub
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
    integrations: IntegrationHub | None = None

    @property
    def idempotency_key(self) -> str:
        return f"nexusai-{self.run_id}-{self.step_index}"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchDocumentsArgs(_Args):
    query: str = Field(min_length=2, max_length=500)


class LookupCustomerArgs(_Args):
    email: EmailStr


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


async def _lookup_customer(ctx: ToolContext, args: LookupCustomerArgs) -> dict[str, Any]:
    crm = await ctx.integrations.get("salesforce") if ctx.integrations else None
    if crm is None:
        return {"found": False, "email": str(args.email), "note": "Salesforce is not connected."}
    contact = await crm.find_contact(str(args.email))
    if contact is None:
        return {"found": False, "email": str(args.email), "source": "salesforce"}
    return {"found": True, "email": str(args.email), "source": "salesforce", **contact}


async def _create_ticket(ctx: ToolContext, args: CreateTicketArgs) -> dict[str, Any]:
    ticket = await ctx.session.scalar(
        select(Ticket).where(Ticket.agent_run_id == ctx.run_id, Ticket.step_index == ctx.step_index)
    )
    if ticket is None:
        title = fill_placeholders(args.title, ctx.outputs)
        description = fill_placeholders(args.description, ctx.outputs)
        external: dict[str, str] | None = None
        itsm = await ctx.integrations.get("servicenow") if ctx.integrations else None
        if itsm is not None:
            # External call first: if it fails, nothing is written here and the step fails.
            # If the database write fails afterwards, a retry reuses the same incident
            # thanks to the idempotency key.
            external = await itsm.create_incident(
                title=title,
                description=description,
                priority=args.priority,
                caller_email=str(args.customer_email) if args.customer_email else None,
                correlation_id=str(ctx.run_id),
                idempotency_key=ctx.idempotency_key,
            )
        # A per-organization transaction lock so concurrent runs get consecutive, unique
        # numbers. An advisory lock rather than a row lock on the organization: a row lock
        # would block the integration call log, which references the organization and is
        # written in its own transaction while this one is still open.
        await ctx.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"ticket-number:{ctx.user.organization_id}"},
        )
        last = await ctx.session.scalar(
            select(func.max(Ticket.number)).where(
                Ticket.organization_id == ctx.user.organization_id
            )
        )
        ticket = Ticket(
            organization_id=ctx.user.organization_id,
            number=(last or TICKET_NUMBER_START - 1) + 1,
            title=title,
            description=description,
            priority=args.priority,
            status="open",
            customer_email=args.customer_email,
            external_system="servicenow" if external else None,
            external_id=external["sys_id"] if external else None,
            external_number=external["number"] if external else None,
            external_url=external["url"] if external else None,
            created_by=ctx.user.id,
            agent_run_id=ctx.run_id,
            step_index=ctx.step_index,
        )
        ctx.session.add(ticket)
        await ctx.session.flush()
    number = f"TCK-{ticket.number}"
    # Customers should see the number of the system the ticket lives in.
    ctx.outputs["ticket_number"] = ticket.external_number or number
    result: dict[str, Any] = {
        "ticket_id": str(ticket.id),
        "ticket_number": number,
        "priority": ticket.priority,
    }
    if ticket.external_system:
        result.update(
            external_system=ticket.external_system,
            external_number=ticket.external_number,
            external_url=ticket.external_url,
        )
    return result


async def _notify_customer(ctx: ToolContext, args: NotifyCustomerArgs) -> dict[str, Any]:
    message = await ctx.session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.agent_run_id == ctx.run_id, OutboxMessage.step_index == ctx.step_index
        )
    )
    if message is None:
        subject = fill_placeholders(args.subject, ctx.outputs)
        body = fill_placeholders(args.body, ctx.outputs)
        mailer = await ctx.integrations.get("email") if ctx.integrations else None
        provider_id = None
        if mailer is not None:
            provider_id = await mailer.send(
                to=str(args.to), subject=subject, body=body, idempotency_key=ctx.idempotency_key
            )
        message = OutboxMessage(
            organization_id=ctx.user.organization_id,
            recipient=str(args.to),
            subject=subject,
            body=body,
            status="sent" if mailer else "recorded",
            provider_message_id=provider_id,
            sent_at=datetime.now(UTC) if mailer else None,
            agent_run_id=ctx.run_id,
            step_index=ctx.step_index,
        )
        ctx.session.add(message)
        await ctx.session.flush()
    result = {"message_id": str(message.id), "to": message.recipient, "status": message.status}
    if message.status == "recorded":
        result["note"] = "Recorded in the outbox; not sent (no email connection configured)."
    return result


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
            name="lookup_customer",
            description="Look up a customer by email in the CRM (Salesforce), if connected.",
            args_model=LookupCustomerArgs,
            mutating=False,
            run=_lookup_customer,
        ),
        Tool(
            name="create_ticket",
            description=(
                "Create a support ticket: in ServiceNow if connected, otherwise in NexusAI."
            ),
            args_model=CreateTicketArgs,
            mutating=True,
            run=_create_ticket,
        ),
        Tool(
            name="notify_customer",
            description=(
                "Email a customer (sent through the email connection if configured). Use "
                "{{ticket_number}} to insert the number of a ticket created earlier in the run."
            ),
            args_model=NotifyCustomerArgs,
            mutating=True,
            run=_notify_customer,
        ),
    )
}
