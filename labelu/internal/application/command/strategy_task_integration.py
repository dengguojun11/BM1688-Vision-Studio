from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalize_label(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    if text[0].isupper():
        return text[0].lower() + text[1:]
    return text


class StrategyDatasetPayload(BaseModel):
    s3_uri: str = Field(min_length=1, max_length=1024)
    image_prefix: str = Field(default="images/", min_length=1, max_length=512)
    metadata_key: str = Field(default="metadata.json", min_length=1, max_length=512)

    @field_validator("image_prefix")
    @classmethod
    def normalize_image_prefix(cls, value: str) -> str:
        text = str(value or "").strip().replace("\\", "/")
        text = text.strip("/")
        if not text:
            raise ValueError("image_prefix cannot be empty")
        return f"{text}/"

    @field_validator("metadata_key")
    @classmethod
    def normalize_metadata_key(cls, value: str) -> str:
        text = str(value or "").strip().replace("\\", "/").lstrip("/")
        if not text:
            raise ValueError("metadata_key cannot be empty")
        return text


class StrategyLabelingPayload(BaseModel):
    required: bool = True
    media_type: Literal["IMAGE"] = "IMAGE"
    annotation_type: Literal["bbox"] = "bbox"
    labels: list[str] = Field(default_factory=list)
    completeness: Literal["complete", "partial", "unknown"] = "unknown"
    explicit_negative_scope: list[str] = Field(default_factory=list)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            normalized = _normalize_label(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            items.append(normalized)
        return items

    @model_validator(mode="after")
    def validate_required_labels(self):
        if self.required and not self.labels:
            raise ValueError("labels cannot be empty when labeling.required is true")
        return self


class StrategyTargetPayload(BaseModel):
    device_type: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    scene_type: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)


class CreateStrategyTaskIntegrationCommand(BaseModel):
    schema_version: str = Field(default="v1", min_length=1, max_length=32)
    external_task_id: str = Field(min_length=1, max_length=128)
    task_name: str = Field(min_length=1, max_length=128)
    task_type: Literal["threshold_experiment", "model_capability"]
    dataset: StrategyDatasetPayload
    labeling: StrategyLabelingPayload
    target: StrategyTargetPayload
    experiment: dict[str, Any] = Field(default_factory=dict)
    source_system: str = Field(default="strategy_evolution", max_length=64)
    dataset_category_key: str = Field(default="", max_length=128)
