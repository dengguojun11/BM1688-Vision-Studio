from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DeploymentObservationResponse(BaseModel):
    id: int
    target_id: int
    observation_key: str
    status: str
    fps: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    cpu_percent: float | None = None
    npu_percent: float | None = None
    memory_percent: float | None = None
    temperature_c: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    sampled_at: datetime | None = None


class DeploymentTargetResponse(BaseModel):
    id: int
    release_id: int
    device_id: str
    desired_state: str
    reported_state: str
    target_bundle_sha256: str
    reported_bundle_sha256: str | None = None
    agent_version: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    last_heartbeat_at: datetime | None = None
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    observations: list[DeploymentObservationResponse] = Field(default_factory=list)
    fencing_token: int = 0


class DeploymentReleaseResponse(BaseModel):
    id: int
    project_id: int
    model_version_id: int
    release_key: str
    status: str
    target_device_ids: list[str] = Field(default_factory=list)
    gray_ratio: float
    observation_seconds: int
    rollback_model_version_id: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_by: int
    approved_by: int | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    targets: list[DeploymentTargetResponse] = Field(default_factory=list)
    fencing_token: int = 0


class DeploymentTransitionAuditResponse(BaseModel):
    id: int
    release_id: int
    target_id: int | None = None
    operation: str
    actor_type: str
    actor_id: int | None = None
    agent_id: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    result: str
    reason: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    fencing_token: int | None = None
    before_snapshot: dict[str, Any] = Field(default_factory=dict)
    after_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
