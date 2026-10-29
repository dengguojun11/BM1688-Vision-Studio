from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateDeploymentReleaseCommand(BaseModel):
    project_id: int = Field(gt=0)
    model_version_id: int = Field(gt=0)
    target_device_ids: list[str] = Field(min_items=1, max_items=500)
    target_bundle_sha256: str = Field(min_length=64, max_length=64)
    gray_ratio: float = Field(default=0.05, gt=0, le=1)
    observation_seconds: int = Field(default=900, ge=60, le=604800)
    rollback_model_version_id: int | None = Field(default=None, gt=0)


class ApproveDeploymentReleaseCommand(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_status: str | None = Field(default=None, min_length=1, max_length=32)
    fencing_token: int | None = Field(default=None, ge=0)


class DeploymentTransitionCommand(BaseModel):
    """Control-plane command used by the deployment release state machine.

    ``expected_status`` makes an operation compare-and-set friendly for UI
    retries: a stale action is rejected with 409 instead of moving a release
    that has already changed in another worker.  Repeating an action after it
    reached its target state remains idempotent.
    """

    reason: str | None = Field(default=None, max_length=4000)
    expected_status: str | None = Field(default=None, min_length=1, max_length=32)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
    fencing_token: int | None = Field(default=None, ge=0)
    force: bool = False


class ReportDeploymentTargetCommand(BaseModel):
    reported_state: Literal[
        "UNKNOWN",
        "DOWNLOADING",
        "STAGED",
        "ACTIVATING",
        "ACTIVE",
        "FAILED",
        "ROLLED_BACK",
    ]
    reported_bundle_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    agent_version: str | None = Field(default=None, max_length=64)
    capabilities: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    error_message: str | None = Field(default=None, max_length=4000)
    expected_status: str | None = Field(default=None, min_length=1, max_length=32)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
    fencing_token: int | None = Field(default=None, ge=0)


class CreateDeploymentObservationCommand(BaseModel):
    observation_key: str = Field(min_length=1, max_length=160)
    status: Literal["HEALTHY", "DEGRADED", "UNHEALTHY"] = "HEALTHY"
    fps: float | None = Field(default=None, ge=0)
    latency_p95_ms: float | None = Field(default=None, ge=0)
    latency_p99_ms: float | None = Field(default=None, ge=0)
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    npu_percent: float | None = Field(default=None, ge=0, le=100)
    memory_percent: float | None = Field(default=None, ge=0, le=100)
    temperature_c: float | None = None
    metrics: dict[str, Any] | None = None
    error_message: str | None = Field(default=None, max_length=4000)
    expected_status: str | None = Field(default=None, min_length=1, max_length=32)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
    fencing_token: int | None = Field(default=None, ge=0)
