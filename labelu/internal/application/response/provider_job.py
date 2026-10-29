from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderJobResponse(BaseModel):
    id: int
    provider_key: str
    provider_version: str
    model_version_id: int | None = None
    input_asset_ids: list[int] = Field(default_factory=list)
    contract_version: str
    config: dict[str, Any] = Field(default_factory=dict)
    request_signature: str
    status: str
    total_count: int = 0
    completed_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PredictionSetResponse(BaseModel):
    id: int
    provider_job_id: int
    sample_id: int | None = None
    version: int
    annotation_contract_version: str
    prediction_schema_version: str
    payload_uri: str | None = None
    payload_sha256: str | None = None
    result_manifest_uri: str | None = None
    result_sha256: str | None = None
    payload: dict[str, Any] | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    status: str
    review_status: str
    source: str = "AI"
    item_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    created_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PredictionSetProjectionResponse(BaseModel):
    id: int
    prediction_set_id: int
    provider_job_id: int
    task_id: int
    sample_id: int
    pre_annotation_id: int
    payload_sha256: str | None = None
    status: str
    idempotency_key: str
    created_by: int
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PredictionSetReviewAuditResponse(BaseModel):
    id: int
    prediction_set_id: int
    from_status: str
    to_status: str
    reason: str | None = None
    actor_id: int
    idempotency_key: str
    created_at: datetime | None = None
