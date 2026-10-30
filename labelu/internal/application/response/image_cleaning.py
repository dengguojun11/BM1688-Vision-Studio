"""Response models for the image-cleaning policy."""

from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, Field


class ImageCleaningPresetResponse(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)


class ImageCleaningPolicyResponse(BaseModel):
    active_preset: str = "standard"
    presets: Dict[str, ImageCleaningPresetResponse] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)
    algorithm_version: str = "visual-v1"
    config_backend_label: str = "MySQL 治理表（system_config_*）"


# Keep the longer alias available for callers that name the nested model after
# the policy rather than the preset.  Both names describe the same wire shape.
ImageCleaningPolicyPresetResponse = ImageCleaningPresetResponse
