from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, root_validator, validator


class CreateIngestionJobCommand(BaseModel):
    source_connection_id: int | None = Field(default=None, gt=0)
    source_type: str = Field(default="UPLOAD", min_length=1, max_length=32)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    source_snapshot: dict[str, Any] | None = None
    selection: dict[str, Any] | None = None

    @validator("source_type")
    def normalize_source_type(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"UPLOAD", "S3", "RTSP", "FILE", "THIRD_PARTY"}:
            raise ValueError("source_type 不受支持")
        return value


class RegisterMediaAssetCommand(BaseModel):
    ingestion_job_id: int | None = Field(default=None, gt=0)
    object_uri: str | None = Field(default=None, min_length=1, max_length=1024)
    asset_uri: str | None = Field(default=None, min_length=1, max_length=1024)
    source_object_key: str | None = Field(default=None, max_length=1024)
    media_type: str = Field(min_length=1, max_length=32)
    content_type: str | None = Field(default=None, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    frame_index: int | None = Field(default=None, ge=0)
    scan_status: str = Field(default="PENDING", max_length=24)
    scan_provider: str | None = Field(default=None, max_length=64)
    scan_signature: str | None = Field(default=None, max_length=256)
    scan_error: str | None = None
    quality_status: str = Field(default="PENDING", max_length=24)
    lifecycle_status: str = Field(default="ACTIVE", max_length=24)
    metadata: dict[str, Any] | None = None

    @root_validator(skip_on_failure=True)
    def require_uri(cls, values):
        uri = values.get("object_uri") or values.get("asset_uri")
        if not uri:
            raise ValueError("object_uri 或 asset_uri 至少提供一个")
        values["object_uri"] = uri
        return values

    @validator("media_type", "scan_status", "quality_status", "lifecycle_status")
    def normalize_upper(cls, value: str) -> str:
        return value.strip().upper()

    @validator("sha256")
    def normalize_sha256(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("sha256 必须是 64 位十六进制摘要")
        return value


class RegisterUploadedAssetCommand(BaseModel):
    """Link a completed upload session into a user/task-scoped ingestion job."""

    upload_session_id: str = Field(min_length=1, max_length=64)
