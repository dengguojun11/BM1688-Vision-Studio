from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ExperimentDatasetImportAcceptedResponse(BaseModel):
    accepted: bool = True
    completed: bool = False
    success: bool | None = None
    package_id: str
    import_job_id: int
    status: str
    status_label: str = ""
    stage: str = ""
    status_detail: str = ""
    progress: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    status_url: str = ""
    last_error: str | None = None
    retry_of_import_job_id: int | None = None
    retry_attempt: int = 0
    retry_reason: str | None = None
    dataset_type: str = ""
    dataset_date: str = ""
    window_start: str = ""
    window_end: str = ""
    target: dict[str, Any] = Field(default_factory=dict)
    declared_sample_count: int = 0
    formal_task_ids: list[int] = Field(default_factory=list)
    draft_task_id: int | None = None
    formal_sample_count: int = 0
    draft_sample_count: int = 0
    abnormal_sample_count: int = 0
    valid_sample_count: int = 0
    required_sample_count: int = 0
    aggregation_key: str = ""
    model_resolution: dict[str, Any] = Field(default_factory=dict)
    dataset_category_id: int | None = None
    dataset_category_key: str | None = None
    dataset_category_name: str | None = None


class ExperimentDatasetImportListItemResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package_id: str
    source_type: str = "internal"
    source_system: str | None = None
    source_external_task_id: str | None = None
    source_external_package_id: str | None = None
    source_integration_id: int | None = None
    import_job_id: int
    status: str
    completed: bool = False
    success: bool | None = None
    message: str = ""
    status_url: str = ""
    last_error: str | None = None
    retry_of_import_job_id: int | None = None
    retry_attempt: int = 0
    retry_reason: str | None = None
    status_label: str = ""
    stage: str = ""
    status_detail: str = ""
    progress: dict[str, Any] = Field(default_factory=dict)
    dataset_type: str = ""
    dataset_date: str
    window_start: str = ""
    window_end: str = ""
    event_type: str
    model_id: str = Field(
        default="",
        validation_alias=AliasChoices("modelId", "model_id"),
        serialization_alias="modelId",
    )
    target_signature: str = ""
    s3_uri: str = ""
    metadata_key: str = ""
    dataset: dict[str, Any] = Field(default_factory=dict)
    labeling: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    declared_sample_count: int = 0
    formal_task_ids: list[int] = Field(default_factory=list)
    draft_task_id: int | None = None
    formal_sample_count: int = 0
    draft_sample_count: int = 0
    abnormal_sample_count: int = 0
    valid_sample_count: int = 0
    required_sample_count: int = 0
    aggregation_key: str = ""
    model_resolution: dict[str, Any] = Field(default_factory=dict)
    dataset_category_id: int | None = None
    dataset_category_key: str | None = None
    dataset_category_name: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


class ExperimentDatasetCreatedTaskResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: int
    source_type: str = "internal"
    source_system: str | None = None
    source_external_task_id: str | None = None
    source_external_package_id: str | None = None
    source_integration_id: int | None = None
    task_url: str = ""
    name: str = ""
    description: str | None = None
    media_type: str = ""
    status: str = ""
    created_at: datetime | None = None
    package_id: str
    import_job_id: int
    import_status: str = ""
    dataset_date: str = ""
    event_type: str = ""
    model_id: str = Field(
        default="",
        validation_alias=AliasChoices("modelId", "model_id"),
        serialization_alias="modelId",
    )
    target: dict[str, Any] = Field(default_factory=dict)
    formal_sample_count: int = 0
    draft_sample_count: int = 0
    abnormal_sample_count: int = 0
    valid_sample_count: int = 0
    required_sample_count: int = 0
    aggregation_key: str = ""
    model_resolution: dict[str, Any] = Field(default_factory=dict)
    dataset_category_id: int | None = None
    dataset_category_key: str | None = None
    dataset_category_name: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)


class ExperimentDatasetImportDetailResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package_id: str
    source_type: str = "internal"
    source_system: str | None = None
    source_external_task_id: str | None = None
    source_external_package_id: str | None = None
    source_integration_id: int | None = None
    import_job_id: int
    status: str
    completed: bool = False
    success: bool | None = None
    message: str = ""
    status_url: str = ""
    status_label: str = ""
    stage: str = ""
    status_detail: str = ""
    progress: dict[str, Any] = Field(default_factory=dict)
    dataset_type: str = ""
    dataset_date: str
    window_start: str = ""
    window_end: str = ""
    event_type: str
    model_id: str = Field(
        default="",
        validation_alias=AliasChoices("modelId", "model_id"),
        serialization_alias="modelId",
    )
    target_signature: str = ""
    s3_uri: str = ""
    metadata_key: str = ""
    dataset: dict[str, Any] = Field(default_factory=dict)
    labeling: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    declared_sample_count: int = 0
    formal_task_ids: list[int] = Field(default_factory=list)
    draft_task_id: int | None = None
    formal_sample_count: int = 0
    draft_sample_count: int = 0
    abnormal_sample_count: int = 0
    valid_sample_count: int = 0
    required_sample_count: int = 0
    aggregation_key: str = ""
    model_resolution: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    retry_of_import_job_id: int | None = None
    retry_attempt: int = 0
    retry_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    dataset_category_id: int | None = None
    dataset_category_key: str | None = None
    dataset_category_name: str | None = None
