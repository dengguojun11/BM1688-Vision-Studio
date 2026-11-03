from typing import Union
from datetime import datetime

from pydantic import BaseModel, Field


class AutoLabelResponse(BaseModel):
    status: str = Field(description="auto-label job status")
    task_id: int = Field(description="task id")
    sample_id: int = Field(description="sample id")
    media_type: str = Field(description="media type")
    provider: str = Field(description="ai provider")
    model: Union[str, None] = Field(default=None, description="model name")
    latency_ms: Union[int, None] = Field(default=None, description="model latency in milliseconds")
    pre_annotation_id: Union[int, None] = Field(default=None, description="created or reused pre-annotation id")
    warning_message: Union[str, None] = Field(default=None, description="non-blocking warning message")


class AutoLabelJobResponse(BaseModel):
    id: int
    task_id: int
    status: str
    sample_count: int
    processed_count: int
    success_count: int
    failed_count: int
    error_message: Union[str, None] = None
    created_at: Union[datetime, None] = None


class AutoLabelQueueJob(BaseModel):
    job_id: int
    task_id: int
    task_name: str
    status: str
    processed_count: int
    sample_count: int
    remaining_count: int


class AutoLabelQueueResponse(BaseModel):
    job_id: int
    status: str
    queue_position: int = 0
    ahead_count: int = 0
    queue_total: int = 0
    ahead_remaining_samples: int = 0
    remaining_samples: int = 0
    current_job: Union[AutoLabelQueueJob, None] = None
    ahead_jobs: list[AutoLabelQueueJob] = Field(default_factory=list)
    estimated_wait_seconds: Union[int, None] = None
    estimated_start_at: Union[datetime, None] = None
    rate_seconds_per_sample: Union[float, None] = None
    note: str = ""
