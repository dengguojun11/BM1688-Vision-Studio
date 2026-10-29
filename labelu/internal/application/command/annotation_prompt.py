from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, validator


class AnnotationPromptPreviewCommand(BaseModel):
    operation: str = Field(default="bbox_detection", min_length=1, max_length=64)
    provider_key: str = Field(default="volcengine", min_length=1, max_length=64)
    model_name: Optional[str] = Field(default=None, max_length=256)
    profile_key: str = Field(default="annotation_bbox_v1", min_length=1, max_length=128)
    profile_version: str = Field(default="1", min_length=1, max_length=32)
    image_width: int = Field(default=1920, ge=1, le=20000)
    image_height: int = Field(default=1080, ge=1, le=20000)

    @validator("operation", "provider_key", "profile_key", "profile_version", pre=True)
    def strip_required_text(cls, value):
        return str(value or "").strip()

    @validator("model_name", pre=True)
    def normalize_model(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class SaveAnnotationPromptCommand(AnnotationPromptPreviewCommand):
    generated_prompt: Optional[str] = Field(default=None, max_length=200000)
    edited_prompt: Optional[str] = Field(default=None, max_length=200000)
    effective_prompt: Optional[str] = Field(default=None, max_length=200000)
    expected_label_set_signature: Optional[str] = Field(default=None, max_length=64)
    expected_class_order_signature: Optional[str] = Field(default=None, max_length=64)
    expected_prompt_id: Optional[int] = Field(default=None, gt=0)
    metadata: Optional[Dict[str, Any]] = None

    @validator("generated_prompt", "edited_prompt", "effective_prompt", pre=True)
    def normalize_prompt_text(cls, value):
        if value is None:
            return None
        return str(value)


class PublishAnnotationPromptCommand(BaseModel):
    expected_contract_revision: Optional[int] = Field(default=None, ge=1)
    expected_prompt_sha256: Optional[str] = Field(default=None, max_length=64)
