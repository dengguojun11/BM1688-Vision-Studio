"""Commands used by the edge validation queue center.

The queue center is intentionally read/write-light.  Query models live in the
application layer so the HTTP adapter can reuse the same validation when a
future non-HTTP consumer (for example an operations worker) is added.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EdgeQueueListQuery(BaseModel):
    """Server-side filters for the edge validation task center."""

    export_id: int | None = Field(default=None, gt=0)
    task_id: int | None = Field(default=None, gt=0)
    keyword: str | None = Field(default=None, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    source_type: str | None = Field(default=None, max_length=64)
    source_system: str | None = Field(default=None, max_length=64)
    pipeline_origin: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)
    active_only: bool = False
    page: int = Field(default=0, ge=0)
    size: int = Field(default=20, ge=1, le=100)

    @field_validator(
        "device_id",
        "source_type",
        "source_system",
        "pipeline_origin",
        "status",
        "keyword",
        mode="before",
    )
    @classmethod
    def normalize_text_filter(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class EdgeQueueRetryCommand(BaseModel):
    """Optional operator explanation recorded by the caller."""

    reason: str | None = Field(default=None, max_length=500)


class EdgeQueueCancelCommand(BaseModel):
    """Optional operator explanation recorded by the caller."""

    reason: str | None = Field(default=None, max_length=500)
