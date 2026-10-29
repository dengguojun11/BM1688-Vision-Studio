from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateEdgeDeviceJobCommand(BaseModel):
    export_job_id: int = Field(gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class ClaimEdgeDeviceJobCommand(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)


class SubmitEdgeDeviceJobResultCommand(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    status: Literal["COMPLETED", "FAILED"]
    result: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
