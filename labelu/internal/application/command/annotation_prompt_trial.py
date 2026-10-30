"""Inputs for a one-image, non-persistent AI prompt trial."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AnnotationPromptTrialCommand(BaseModel):
    """Runtime options carried alongside the multipart image upload.

    The image itself is intentionally kept out of this model.  Routers read
    the upload bytes and pass them to the service, so a trial cannot be
    mistaken for a task sample write.
    """

    prompt_id: Optional[int] = Field(default=None, gt=0)
    prompt_override: Optional[str] = Field(default=None, max_length=200000)
    prompt_text: Optional[str] = Field(
        default=None,
        max_length=200000,
        description="兼容前端字段；与 prompt_override 不能同时传不同内容",
    )
    operation: str = Field(default="bbox_detection", min_length=1, max_length=64)
    provider_key: str = Field(default="volcengine", min_length=1, max_length=64)
    model_name: Optional[str] = Field(default=None, max_length=256)
    profile_key: str = Field(default="annotation_bbox_v1", min_length=1, max_length=128)
    profile_version: str = Field(default="1", min_length=1, max_length=32)

    @field_validator("operation", "provider_key", "profile_key", "profile_version")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("model_name", "prompt_override", "prompt_text")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value)
        return value if value.strip() else None
