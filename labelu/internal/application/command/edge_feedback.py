from pydantic import BaseModel, Field


class CreateInferenceEventCommand(BaseModel):
    # Historical edge clients may still send project_id. New clients omit it;
    # the platform stores these events in the global evolution scope.
    project_id: int | None = Field(default=None, gt=0)
    device_id: str = Field(min_length=1, max_length=128)
    event_key: str = Field(min_length=1, max_length=160)
    event_type: str = Field(default="LOW_CONFIDENCE", max_length=32)
    model_version_id: int | None = Field(default=None, gt=0)
    confidence: int | None = Field(default=None, ge=0, le=100)
    asset_uri: str | None = Field(default=None, max_length=1024)
    asset_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    payload: dict = Field(default_factory=dict)
    fingerprint: str = Field(min_length=8, max_length=128)


class ReviewHardExampleCommand(BaseModel):
    decision: str = Field(pattern="^(ACCEPTED|REJECTED)$")
    reason: str | None = Field(default=None, max_length=2000)
