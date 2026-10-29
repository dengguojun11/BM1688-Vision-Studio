from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestionJobResponse(BaseModel):
    id: int
    source_connection_id: int | None = None
    source_type: str
    idempotency_key: str
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    selection: dict[str, Any] = Field(default_factory=dict)
    status: str
    manifest_sha256: str | None = None
    total_count: int = 0
    accepted_count: int = 0
    quarantined_count: int = 0
    failed_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class MediaAssetResponse(BaseModel):
    id: int
    ingestion_job_id: int | None = None
    object_uri: str
    # Detailed ADR spelling retained as a response alias for clients that use
    # ``asset_uri``; both values are sourced from one database column.
    asset_uri: str
    source_object_key: str | None = None
    media_type: str
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    frame_index: int | None = None
    scan_status: str
    scan_provider: str | None = None
    scan_signature: str | None = None
    scan_error: str | None = None
    scanned_at: datetime | None = None
    quality_status: str
    lifecycle_status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachment_id: int | None = None
    created_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
