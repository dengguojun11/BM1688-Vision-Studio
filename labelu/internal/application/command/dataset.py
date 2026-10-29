from typing import Any, Literal

from pydantic import BaseModel, Field, validator

DatasetVersionStatus = Literal[
    "INGESTING", "VALIDATING", "READY_TO_REVIEW", "FROZEN", "RETIRED"
]


class CreateDatasetCommand(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    dataset_category_id: int | None = Field(default=None, gt=0)
    label_ids: list[int] = Field(default_factory=list)
    annotation_kind: str = Field(default="bbox", min_length=1, max_length=32)

    @validator("label_ids", pre=True)
    def normalize_label_ids(cls, value):
        if value is None:
            return []
        result = []
        seen = set()
        for item in value:
            label_id = int(item)
            if label_id <= 0:
                raise ValueError("label_ids 只能包含正整数")
            if label_id not in seen:
                seen.add(label_id)
                result.append(label_id)
        return result


class UpdateDatasetCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    dataset_category_id: int | None = Field(default=None, gt=0)


class CreateDatasetLabelContractCommand(BaseModel):
    label_ids: list[int] = Field(min_items=1, max_items=500)
    annotation_kind: str = Field(default="bbox", min_length=1, max_length=32)

    @validator("label_ids", pre=True)
    def normalize_label_ids(cls, value):
        if value is None:
            raise ValueError("label_ids 不能为空")
        result = []
        seen = set()
        for item in value:
            label_id = int(item)
            if label_id <= 0:
                raise ValueError("label_ids 只能包含正整数")
            if label_id not in seen:
                seen.add(label_id)
                result.append(label_id)
        if not result:
            raise ValueError("label_ids 不能为空")
        return result


class CreateDatasetVersionCommand(BaseModel):
    manifest_rel_path: str = Field(min_length=1, max_length=512)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    label_set_signature: str | None = Field(default=None, max_length=64)
    class_order_signature: str | None = Field(default=None, max_length=64)
    sample_count: int = Field(default=0, ge=0)
    quality_status: str | None = Field(default=None, max_length=32)
    metadata_json: Any = None


class CreateDatasetVersionFromTasksCommand(BaseModel):
    """Business-facing dataset version creation request.

    The manifest and label-contract fields are deliberately absent here. They
    are generated from the selected, completed annotation tasks by the service.
    """

    source_task_ids: list[int] = Field(min_items=1, max_items=200)
    sample_ids: list[int] | None = Field(default=None, max_items=100000)
    train_ratio: float = Field(default=0.8, gt=0, lt=1)
    val_ratio: float = Field(default=0.1, gt=0, lt=1)
    test_ratio: float = Field(default=0.1, gt=0, lt=1)
    note: str | None = Field(default=None, max_length=1000)


class UpdateDatasetVersionCommand(BaseModel):
    manifest_rel_path: str | None = Field(default=None, min_length=1, max_length=512)
    manifest_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    label_set_signature: str | None = Field(default=None, max_length=64)
    class_order_signature: str | None = Field(default=None, max_length=64)
    sample_count: int | None = Field(default=None, ge=0)
    quality_status: str | None = Field(default=None, max_length=32)
    metadata_json: Any = None
    status: DatasetVersionStatus | None = None


class ReviewDatasetVersionCommand(BaseModel):
    """Human decision for a technically validated dataset version."""

    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = Field(default=None, max_length=2000)
