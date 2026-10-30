from __future__ import annotations

from pydantic import BaseModel, Field

from labelu.internal.application.command.sample import CreateSampleCommand


class ImageCleaningPreviewCommand(BaseModel):
    attachment_ids: list[int] = Field(..., min_length=1)
    policy_revision: int | None = Field(default=None, ge=1)


class ImageCleaningCommitCommand(BaseModel):
    attachment_ids: list[int] = Field(..., min_length=1)
    policy_revision: int = Field(..., ge=1)
    fingerprint: str = Field(..., min_length=16, max_length=256)


class CreateSamplesWithImageCleaningCommand(BaseModel):
    # An upload batch may contain only duplicates.  In that case the request
    # still needs to reach the cleaning service so it can physically delete
    # the duplicate attachments while creating zero samples.
    samples: list[CreateSampleCommand] = Field(..., min_length=0)
    cleaning: ImageCleaningCommitCommand
