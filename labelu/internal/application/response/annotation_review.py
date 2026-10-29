from datetime import datetime

from pydantic import BaseModel


class AnnotationReviewResponse(BaseModel):
    id: int
    task_id: int
    sample_id: int
    annotation_sha256: str
    source_type: str
    review_status: str
    review_reason: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    arbitrator_id: int | None = None
    arbitrated_at: datetime | None = None
    arbitration_decision: str | None = None
    arbitration_reason: str | None = None
    invalidated_by: int | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    created_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
