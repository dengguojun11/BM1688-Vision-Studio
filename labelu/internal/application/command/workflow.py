from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

WorkflowType = Literal[
    "expert_feedback",
    "threshold_experiment",
    "model_optimization",
    "third_party_callback",
]
WorkflowStatus = Literal[
    "PENDING", "RUNNING", "WAITING", "SUCCEEDED", "FAILED", "DEAD_LETTER", "CANCELLED"
]


class CreateWorkflowCommand(BaseModel):
    # Optional for new project-free evolution workflows; historical callers
    # may continue to send a project ID.
    project_id: int | None = Field(default=None, gt=0)
    workflow_type: WorkflowType
    idempotency_key: str = Field(min_length=1, max_length=160)
    steps: list[str] = Field(min_length=1, max_length=64)
    payload: dict[str, Any] | None = None
    max_attempts: int = Field(default=5, ge=1, le=20)
    owner_id: int | None = Field(default=None, gt=0)
    approver_id: int | None = Field(default=None, gt=0)
    deadline_at: datetime | None = None
    callback_url: str | None = Field(default=None, max_length=512)
    callback_signature: str | None = Field(default=None, max_length=128)


class TransitionWorkflowCommand(BaseModel):
    status: WorkflowStatus
    step_key: str | None = Field(default=None, max_length=96)
    output: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=4000)
