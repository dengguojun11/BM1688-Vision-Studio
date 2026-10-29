from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EdgeThresholdsResponse(BaseModel):
    threshold_unit: Literal["ratio_0_1"] = "ratio_0_1"
    unit: Literal["ratio_0_1"] = "ratio_0_1"
    conf_thres: float
    nms_thres: float
    match_iou_thres: float
    source: str | None = None
    reason: str | None = None
    confidence_level: str | None = None
    provider_key: str | None = None
    requested: dict[str, Any] | None = None
    defaulted_fields: list[str] = Field(default_factory=list)


class EdgeThresholdsSnapshotResponse(BaseModel):
    threshold_unit: Literal["ratio_0_1"] = "ratio_0_1"
    unit: Literal["ratio_0_1"] = "ratio_0_1"
    conf_thres: float | None = None
    nms_thres: float | None = None
    match_iou_thres: float | None = None
    source: str | None = None
    reason: str | None = None
    confidence_level: str | None = None
    provider_key: str | None = None
    requested: dict[str, Any] | None = None
    defaulted_fields: list[str] = Field(default_factory=list)


class YoloEdgeValidationProgressResponse(BaseModel):
    stage: str
    message: str
    detail: dict[str, Any] | None = None
    updated_at: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class YoloEdgeValidationJobRowResponse(BaseModel):
    id: int
    export_id: int
    task_id: int
    task_name: str | None = None
    source_type: str = "internal"
    source_system: str | None = None
    pipeline_origin: str | None = None
    source_external_task_id: str | None = None
    source_external_package_id: str | None = None
    source_integration_id: int | None = None
    status: str
    # ``status`` remains the persisted lifecycle value for backwards
    # compatibility.  Queue/operator surfaces use this derived projection so
    # preparation, retry, blocked, and running lanes are rendered uniformly.
    display_status: str | None = None
    mode: str
    execution_profile: Literal["quick", "certification"] = "quick"
    image_count: int
    labeled_image_count: int
    unlabeled_image_count: int
    threshold_source: str | None = None
    edge_device_id: str | None = None
    edge_device_name: str | None = None
    edge_host: str | None = None
    edge_port: int | None = None
    edge_user: str | None = None
    queue_reason: str | None = None
    queue_position: int | None = None
    queued_at: datetime | None = None
    assigned_at: datetime | None = None
    lease_expires_at: datetime | None = None
    thresholds: EdgeThresholdsSnapshotResponse | None = None
    ai_thresholds: dict[str, Any] | None = None
    workflow_stage: str = "edge_validation"
    edge_result_status: str = "unknown"
    report_summary: str | None = None
    progress: YoloEdgeValidationProgressResponse | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class YoloEdgeValidationJobDetailResponse(YoloEdgeValidationJobRowResponse):
    sample_ids: list[int] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)
    external_attachments: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: EdgeThresholdsResponse
    result: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # The detail view uses the same timing contract as the queue center.  The
    # values are snapshot-relative for active jobs and stop at the persisted
    # transition for terminal jobs.
    waiting_seconds: int | None = None
    running_seconds: int | None = None
    estimated_seconds: int | None = None


class EdgeThresholdRecommendationResponse(BaseModel):
    recommended_thresholds: EdgeThresholdsResponse
    fallback_used: bool = False


class EdgeThresholdRefinementResponse(BaseModel):
    stage: str = "post_validation_refine"
    base_thresholds: EdgeThresholdsResponse
    refined_thresholds: EdgeThresholdsResponse
    delta_summary: dict[str, float] = Field(default_factory=dict)
    fallback_used: bool = False
