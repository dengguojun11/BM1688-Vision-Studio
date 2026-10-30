from __future__ import annotations

from pydantic import BaseModel, Field


class ImageCleaningPairResponse(BaseModel):
    keep_id: int
    duplicate_id: int
    similarity: float = Field(ge=0.0, le=1.0)


class ImageCleaningPreviewResponse(BaseModel):
    active_preset: str
    threshold: float = Field(ge=0.0, le=1.0)
    policy_revision: int = Field(ge=1)
    algorithm_version: str
    total_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    keep_count: int = Field(ge=0)
    duplicate_attachment_ids: list[int] = Field(default_factory=list)
    keep_attachment_ids: list[int] = Field(default_factory=list)
    pairs: list[ImageCleaningPairResponse] = Field(default_factory=list)
    fingerprint: str
    # Candidate ids are the submitted attachments that were readable and had
    # no active sample/pre-annotation reference.  Older clients can continue
    # to derive this set from keep + duplicate ids.
    candidate_attachment_ids: list[int] = Field(default_factory=list)
    skipped_attachment_ids: list[int] = Field(default_factory=list)
    skipped_count: int = Field(default=0, ge=0)


class ImageCleaningCreateSamplesResponse(BaseModel):
    ids: list[int] = Field(default_factory=list)
    deleted_attachment_ids: list[int] = Field(default_factory=list)
    deleted_count: int = Field(ge=0)
    kept_count: int = Field(ge=0)
