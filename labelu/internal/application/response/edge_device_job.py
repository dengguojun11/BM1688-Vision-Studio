from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EdgeDeviceJobResponse(BaseModel):
    id: int
    export_job_id: int
    task_id: int
    status: str
    device_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_message: str | None = None
    download_url: str | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EdgeDeviceClaimResponse(BaseModel):
    job: EdgeDeviceJobResponse | None = None
