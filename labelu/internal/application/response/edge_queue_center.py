"""Response contracts for the edge validation task center."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EdgeQueueCountsResponse(BaseModel):
    """Stable, display-oriented counts for the queue header."""

    preparing: int = 0
    queued: int = 0
    processing: int = 0
    retrying: int = 0
    blocked: int = 0
    failed: int = 0
    completed: int = 0
    cancelled: int = 0
    active: int = 0
    total: int = 0


class EdgeQueueDeviceResponse(BaseModel):
    """A privacy-safe device lane in the queue center."""

    device_id: str
    device_name: str = ""
    status: str = "unknown"
    is_online: bool = False
    is_ready: bool = False
    is_busy: bool = False
    current_job_id: int | None = None
    current_job_status: str | None = None
    current_job_started_at: datetime | None = None
    next_job_id: int | None = None
    next_queue_position: int | None = None
    pending_count: int = 0
    eta_seconds: int | None = None


class EdgeQueueSummaryResponse(BaseModel):
    generated_at: datetime
    counts: EdgeQueueCountsResponse
    oldest_queued_at: datetime | None = None
    oldest_wait_seconds: int | None = None
    estimated_clear_seconds: int | None = None
    eta_confidence: str = "low"
    eta_basis: str = "暂无同类历史任务"
    completed_duration_samples: int = 0
    devices: list[EdgeQueueDeviceResponse] = Field(default_factory=list)


class EdgeQueueJobResponse(BaseModel):
    """Compact row used by the high-volume task-center table."""

    id: int
    export_id: int
    task_id: int
    task_name: str | None = None
    status: str
    # ``status`` is the operator-facing projection returned by this endpoint.
    # Keep the explicit field as well so clients can share the same response
    # contract with the legacy job/detail endpoints during a rolling upgrade.
    display_status: str | None = None
    source_type: str = "internal"
    source_system: str | None = None
    pipeline_origin: str | None = None
    mode: str = "run_only"
    execution_profile: str = "quick"
    image_count: int = 0
    labeled_image_count: int = 0
    unlabeled_image_count: int = 0
    edge_device_id: str | None = None
    edge_device_name: str | None = None
    queue_reason: str | None = None
    queue_position: int | None = None
    queued_at: datetime | None = None
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    waiting_seconds: int | None = None
    running_seconds: int | None = None
    estimated_seconds: int | None = None
    error_message: str | None = None
    report_summary: str | None = None
    progress: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EdgeQueueJobListResponse(BaseModel):
    items: list[EdgeQueueJobResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 0
    size: int = 20


class EdgeQueueMutationResponse(BaseModel):
    job_id: int
    status: str
    message: str
    queue_position: int | None = None
