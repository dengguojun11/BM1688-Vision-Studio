from datetime import datetime

from pydantic import BaseModel, Field


class DatasetResponse(BaseModel):
    id: int
    owner_id: int
    name: str
    description: str | None = None
    dataset_category_id: int | None = None
    dataset_category_name: str | None = None
    status: str
    created_by: int
    updated_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version_count: int = 0
    dataset_label_contract_id: int | None = None
    dataset_label_contract_revision: int | None = None
    dataset_label_contract_status: str | None = None
    dataset_label_contract_annotation_kind: str | None = None
    dataset_label_set_signature: str | None = None
    dataset_class_order_signature: str | None = None
    dataset_label_ids: list[int] = Field(default_factory=list)
    dataset_labels: list[dict] = Field(default_factory=list)


class DatasetLabelContractResponse(BaseModel):
    id: int
    dataset_id: int
    revision: int
    annotation_kind: str
    label_ids: list[int] = Field(default_factory=list)
    labels: list[dict] = Field(default_factory=list)
    label_set_signature: str
    class_order_signature: str
    status: str
    created_by: int | None = None
    frozen_by: int | None = None
    created_at: datetime | None = None
    frozen_at: datetime | None = None


class DatasetVersionResponse(BaseModel):
    id: int
    dataset_id: int
    # Included by the global version listing so callers do not need to issue
    # one dataset request per row just to render the table.
    dataset_name: str | None = None
    dataset_category_id: int | None = None
    version_no: int
    status: str
    manifest_rel_path: str
    manifest_sha256: str
    label_set_signature: str | None = None
    class_order_signature: str | None = None
    sample_count: int
    quality_status: str | None = None
    review_status: str | None = None
    review_comment: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    metadata_json: str | None = None
    dataset_category_snapshot_json: str | None = None
    dataset_label_contract_id: int | None = None
    dataset_contract_revision: int | None = None
    labels_snapshot_json: str | None = None
    created_by: int
    frozen_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    frozen_at: datetime | None = None
    retired_at: datetime | None = None


class DatasetVersionValidationResponse(BaseModel):
    version_id: int
    valid: bool
    checks: dict[str, bool]
    warnings: list[str] = Field(default_factory=list)
