from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackClusterResponse(BaseModel):
    id: int
    project_id: int | None
    scope_key: str = "global"
    created_by: int | None = None
    cluster_key: str
    event_type: str | None
    device_id: str | None
    model_version_id: int | None = None
    source_key: str | None = None
    sample_count: int
    average_confidence: int | None
    priority_score: int
    status: str
    created_at: datetime | None
    updated_at: datetime | None


class ActiveLearningBatchItemResponse(BaseModel):
    id: int
    hard_example_id: int
    score: int
    rank: int
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class ActiveLearningBatchResponse(BaseModel):
    id: int
    project_id: int | None
    scope_key: str = "global"
    batch_key: str
    model_version_id: int | None = None
    source_key: str | None = None
    quota: int
    selected_count: int
    accepted_count: int
    status: str
    workflow_id: int | None
    dataset_version_id: int | None
    scoring_policy: dict
    items: list[ActiveLearningBatchItemResponse]
    created_by: int
    created_at: datetime | None
    updated_at: datetime | None
