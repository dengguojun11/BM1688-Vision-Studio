from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


def _normalize_key(value: str, *, keep_trailing_slash: bool = False) -> str:
    text = str(value or "").strip().replace("\\", "/")
    text = text.lstrip("/")
    if keep_trailing_slash:
        text = text.rstrip("/")
        return f"{text}/" if text else ""
    return text.rstrip("/")


class ExperimentDatasetLocationPayload(BaseModel):
    s3_uri: str = Field(min_length=1, max_length=1024)
    bucket: str = Field(min_length=1, max_length=256)
    prefix: str = Field(min_length=1, max_length=1024)
    image_prefix: str = Field(min_length=1, max_length=1024)
    image_s3_prefix: str = Field(default="", max_length=1024)
    metadata_key: str = Field(min_length=1, max_length=1024)
    metadata_s3_uri: str = Field(default="", max_length=1024)
    metadata_file: str = Field(default="metadata.json", max_length=255)
    image_directory: str = Field(default="images/", max_length=255)
    sample_alignment: str = Field(default="", max_length=512)

    @field_validator("prefix", "image_prefix")
    @classmethod
    def normalize_dir_like_keys(cls, value: str) -> str:
        normalized = _normalize_key(value, keep_trailing_slash=True)
        if not normalized:
            raise ValueError("directory-like key cannot be empty")
        return normalized

    @field_validator("metadata_key")
    @classmethod
    def normalize_metadata_key(cls, value: str) -> str:
        normalized = _normalize_key(value, keep_trailing_slash=False)
        if not normalized:
            raise ValueError("metadata_key cannot be empty")
        return normalized


class ExperimentDatasetLabelingPayload(BaseModel):
    required: bool = True
    media_type: Literal["IMAGE"] = "IMAGE"
    annotation_type: Literal["bbox"] = "bbox"
    labels: list[str] = Field(default_factory=list)
    completeness: Literal["complete", "partial", "unknown"] = "unknown"
    explicit_negative_scope: list[str] = Field(default_factory=list)


class ExperimentDatasetTargetPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_type: str = Field(default="", max_length=128)
    event_type: str = Field(default="", max_length=128)
    model_id: str = Field(
        default="",
        max_length=128,
        validation_alias=AliasChoices("modelId", "model_id"),
        serialization_alias="modelId",
    )
    scene_type: str = Field(default="", max_length=128)
    strategy_id: str = Field(default="", max_length=128)


class CreateExperimentDatasetImportCommand(BaseModel):
    schema_version: str = Field(default="v1", min_length=1, max_length=32)
    package_id: str = Field(min_length=1, max_length=128)
    source_type: Literal["internal", "third_party"] = "third_party"
    source_system: str = Field(default="strategy_evolution", max_length=64)
    source_external_task_id: str = Field(default="", max_length=128)
    dataset_type: str = Field(min_length=1, max_length=64)
    dataset_date: str = Field(min_length=1, max_length=32)
    window_start: str = Field(min_length=1, max_length=32)
    window_end: str = Field(min_length=1, max_length=32)
    dataset: ExperimentDatasetLocationPayload
    labeling: ExperimentDatasetLabelingPayload
    target: ExperimentDatasetTargetPayload
    sample_count: int = Field(default=0, ge=0)
    target_boxes_required: bool = True
    dataset_category_key: str = Field(default="", max_length=128)
    aggregation_key: str = Field(
        default="",
        max_length=512,
        description="Optional stable key used to aggregate multiple packages into one task.",
    )
    # Internal compatibility projection.  It is populated by the legacy
    # strategy adapter before the Worker can claim the import row.
    source_integration_id: int | None = Field(default=None, ge=1)


class RetryExperimentDatasetImportCommand(BaseModel):
    """Optional operator context for a failed expert-feedback retry."""

    reason: str = Field(default="", max_length=500)
