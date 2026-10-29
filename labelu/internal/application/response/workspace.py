from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class WorkspaceSummaryResponse(BaseModel):
    task_count: int
    pending_annotation_task_count: int
    model_count: int
    published_model_count: int
    download_disabled_model_count: int = 0
    active_training_count: int = 0
    failed_training_count: int = 0
    pending_feedback_count: int = 0
    generated_at: datetime
