from typing import Literal

from pydantic import BaseModel, Field, validator


class CreateAnnotationReviewCommand(BaseModel):
    task_id: int = Field(gt=0)
    sample_id: int = Field(gt=0)
    annotation_sha256: str = Field(min_length=64, max_length=64)
    source_type: str = Field(default="FORMAL", min_length=1, max_length=32)

    @validator("annotation_sha256")
    def validate_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(
                "annotation_sha256 must be a 64-character hexadecimal hash"
            )
        return value

    @validator("source_type")
    def normalize_source(cls, value: str) -> str:
        return value.strip().upper()


class TransitionAnnotationReviewCommand(BaseModel):
    target_status: Literal[
        "IN_REVIEW", "APPROVED", "REJECTED", "ARBITRATION", "ARBITRATED", "INVALIDATED"
    ]
    reason: str | None = Field(default=None, max_length=2000)
    arbitration_decision: Literal["APPROVED", "REJECTED"] | None = None

    @validator("reason")
    def normalize_reason(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None
