from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStepResponse(BaseModel):
    id: int
    step_key: str
    sequence: int
    status: str
    attempts: int
    max_attempts: int
    available_at: datetime | None = None
    output: dict[str, Any] | None = None
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowEventResponse(BaseModel):
    id: int
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    step_key: str | None = None
    payload: dict[str, Any] | None = None
    actor_id: int | None = None
    created_at: datetime


class WorkflowResponse(BaseModel):
    id: int
    project_id: int | None = None
    scope_key: str
    workflow_type: str
    status: str
    idempotency_key: str
    current_step: str | None = None
    attempts: int
    max_attempts: int
    next_run_at: datetime | None = None
    last_error: str | None = None
    steps: list[WorkflowStepResponse] = Field(default_factory=list)
    events: list[WorkflowEventResponse] = Field(default_factory=list)
    created_by: int
    owner_id: int | None = None
    approver_id: int | None = None
    deadline_at: datetime | None = None
    input_snapshot: dict[str, Any] | None = None
    callback_url: str | None = None
    callback_signature: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
