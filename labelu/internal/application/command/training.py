from typing import Any, Literal, List

from pydantic import BaseModel, Field, validator


class CreateTrainingJobCommand(BaseModel):
    """Platform-level training request shared by all registered algorithms."""

    task_id: int = Field(gt=0)
    # Optional task-selection dimensions captured from the training wizard.
    # They are validated again against the task's current category and label
    # contract so a stale or forged candidate list cannot bypass the filter.
    dataset_category_id: int | None = Field(default=None, gt=0)
    dataset_id: int | None = Field(default=None, gt=0)
    # Legacy callers use ``dataset_id`` as the primary scope.  New callers
    # may send a deduplicated list when a task spans compatible datasets.
    dataset_ids: List[int] = Field(default_factory=list)
    label_ids: List[int] = Field(default_factory=list)
    algorithm_key: str = Field(min_length=1, max_length=128)
    # Normal users start training from a completed task.  The server creates
    # (or reuses) the immutable training snapshot automatically.  An explicit
    # version remains accepted for governance pages and legacy clients.
    dataset_version_id: int | None = Field(default=None, gt=0)
    # Optional model-series lineage.  Nullable fields keep legacy callers
    # valid while new clients can explicitly select an existing branch.
    series_id: int | None = Field(default=None, gt=0)
    variant_id: int | None = Field(default=None, gt=0)
    series_name: str | None = Field(default=None, max_length=128)
    variant_name: str | None = Field(default=None, max_length=128)
    model_family: str | None = Field(default=None, max_length=128)
    parent_model_version_id: int | None = Field(default=None, gt=0)
    parent_training_job_id: int | None = Field(default=None, gt=0)
    branch_key: str | None = Field(default=None, min_length=1, max_length=64)
    inheritance_mode: str | None = Field(default=None, max_length=32)
    selection_contract: dict[str, Any] | None = None
    sample_conditions: dict[str, list[str]] = Field(default_factory=dict)
    model_weights: str = Field(default="", max_length=256)
    # Legacy YOLO weight references are kept as a typed part of the platform
    # contract so the unified creator can reuse the existing access checks.
    weights_source: Literal["preset", "imported", "trained", "auto"] = "preset"
    weights_ref_id: int | None = Field(default=None, gt=0)
    # A recipe is optional for generic container algorithms, but when supplied
    # it is snapshotted into the job lineage for reproducible reruns.
    training_recipe_key: str | None = Field(default=None, max_length=128)
    training_recipe_version: int | None = Field(default=None, ge=1)
    rect_preset_slug: str | None = Field(default=None, max_length=128)
    plate_training_target: Literal[
        "plate_detection", "plate_recognition"
    ] | None = None
    epochs: int = Field(default=50, ge=1, le=3000)
    imgsz: int = Field(default=640, ge=320, le=1280)
    chip_type: str | None = Field(default=None, max_length=64)
    precision: str | None = Field(default=None, max_length=16)
    execution_mode: Literal["local", "remote", "container"] = "local"
    train_node_id: str | None = Field(default=None, max_length=64)
    container_image: str | None = Field(default=None, max_length=256)
    params: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    @validator(
        "algorithm_key",
        "model_weights",
        "chip_type",
        "precision",
        "container_image",
        "training_recipe_key",
        "rect_preset_slug",
        pre=True,
    )
    def normalize_text(cls, value):
        if value is None:
            return value
        normalized = str(value).strip()
        return normalized or None

    @validator("precision")
    def validate_precision(cls, value):
        if value is None:
            return value
        normalized = value.lower()
        if normalized not in {"fp32", "fp16", "int8"}:
            raise ValueError("precision must be one of fp32, fp16, int8")
        return normalized

    @validator("label_ids", pre=True)
    def normalize_label_ids(cls, value):
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("label_ids must be a list")
        result = []
        seen = set()
        for item in value:
            try:
                label_id = int(item)
            except (TypeError, ValueError) as exc:
                raise ValueError("label_ids 只能包含正整数") from exc
            if label_id <= 0:
                raise ValueError("label_ids 只能包含正整数")
            if label_id not in seen:
                seen.add(label_id)
                result.append(label_id)
        return result

    @validator("dataset_ids", pre=True)
    def normalize_dataset_ids(cls, value):
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("dataset_ids must be a list")
        result = []
        for item in value:
            try:
                dataset_id = int(item)
            except (TypeError, ValueError) as exc:
                raise ValueError("dataset_ids 只能包含正整数") from exc
            if dataset_id <= 0:
                raise ValueError("dataset_ids 只能包含正整数")
            if dataset_id not in result:
                result.append(dataset_id)
        return result
