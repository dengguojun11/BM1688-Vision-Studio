from pydantic import BaseModel, Field


class RebuildFeedbackClustersCommand(BaseModel):
    project_id: int | None = Field(default=None, gt=0)
    confidence_threshold: int = Field(default=60, ge=0, le=100)


class CreateActiveLearningBatchCommand(BaseModel):
    project_id: int | None = Field(default=None, gt=0)
    quota: int = Field(default=100, ge=1, le=5000)
    cluster_ids: list[int] | None = Field(default=None, max_length=500)
    batch_key: str | None = Field(default=None, max_length=160)
    model_version_id: int | None = Field(default=None, gt=0)
    source_key: str | None = Field(default=None, max_length=160)


class AcceptActiveLearningBatchCommand(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
