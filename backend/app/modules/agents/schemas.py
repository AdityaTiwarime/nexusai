import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    request: str = Field(min_length=3, max_length=2000)


class Step(BaseModel):
    index: int
    tool: str
    args: dict[str, Any]
    reason: str
    mutating: bool
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class ApprovalBrief(BaseModel):
    id: uuid.UUID
    status: str
    expires_at: datetime
    decided_by_email: str | None = None
    decided_at: datetime | None = None
    comment: str | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request: str
    status: str
    planner: str
    steps: list[Step]
    summary: str | None
    error: str | None
    requested_by: uuid.UUID | None
    requested_by_email: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    approval: ApprovalBrief | None = None


class Action(BaseModel):
    index: int
    tool: str
    args: dict[str, Any]
    reason: str


class ApprovalOut(BaseModel):
    id: uuid.UUID
    agent_run_id: uuid.UUID
    status: str
    request: str
    actions: list[Action]
    requested_by_email: str | None
    expires_at: datetime
    decided_by_email: str | None
    decided_at: datetime | None
    comment: str | None
    created_at: datetime


class Decision(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    title: str
    description: str
    priority: str
    status: str
    customer_email: str | None
    external_system: str | None
    external_number: str | None
    external_url: str | None
    agent_run_id: uuid.UUID | None
    created_at: datetime


class OutboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipient: str
    subject: str
    body: str
    status: str
    provider_message_id: str | None
    sent_at: datetime | None
    agent_run_id: uuid.UUID | None
    created_at: datetime
