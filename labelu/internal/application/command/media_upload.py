from datetime import datetime

from pydantic import BaseModel, Field, validator


class CreateMediaUploadSessionCommand(BaseModel):
    task_id: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=256)
    total_size: int = Field(gt=0, le=20 * 1024 * 1024 * 1024)
    chunk_size: int = Field(default=8 * 1024 * 1024, ge=256 * 1024, le=64 * 1024 * 1024)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @validator("sha256")
    def validate_sha256(cls, value):
        if value is not None and any(ch not in "0123456789abcdefABCDEF" for ch in value):
            raise ValueError("sha256 必须是十六进制摘要")
        return value.lower() if value else value


class MediaUploadSessionResponse(BaseModel):
    session_id: str
    task_id: int
    filename: str
    total_size: int
    chunk_size: int
    total_chunks: int
    received_chunks: list[int]
    status: str
    sha256: str | None = None
    attachment_id: int | None = None
    scan_status: str = "PENDING"
    scan_provider: str | None = None
    scan_signature: str | None = None
    scan_error: str | None = None
    scanned_at: datetime | None = None
    quarantined_at: datetime | None = None
    quarantine_until: datetime | None = None
