from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HardExampleResponse(BaseModel):
    id: int
    project_id: int | None
    scope_key: str = "global"
    created_by: int | None = None
    inference_event_id: int
    device_id: str
    model_version_id: int | None
    fingerprint: str
    asset_uri: str | None
    confidence: int | None
    hard_example_type: str
    review_status: str
    review_reason: str | None
    training_status: str
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
