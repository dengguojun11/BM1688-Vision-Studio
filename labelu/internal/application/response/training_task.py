from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TrainingTaskNameCheckResponse(BaseModel):
    available: bool
    normalized_name: str
    reason: Optional[str] = None


class MergedTrainingClassResponse(BaseModel):
    class_name: str
    class_order: int
    label_key: Optional[str] = None
    label_value: Optional[str] = None
    selected_from: List[str] = Field(default_factory=list)
    image_count: Optional[int] = None


class TrainingTaskIncludedTaskResponse(BaseModel):
    task_id: int
    task_name: str
    included_image_count: int = 0
    matched_done_sample_count: Optional[int] = None
    match_sources: List[str] = Field(default_factory=list)
    positive_image_count: int = 0
    negative_image_count: int = 0
    instance_count: int = 0
    invalid_image_count: int = 0
    contract_id: Optional[int] = None
    contract_revision: Optional[int] = None
    label_ids: List[int] = Field(default_factory=list)


class TrainingTaskIgnoredTaskResponse(BaseModel):
    task_id: int
    task_name: str
    reason_code: str
    reason_text: str


class LabelDimensionTrainingPreviewResponse(BaseModel):
    selected_preset_count: int
    selected_label_item_count: int
    merged_class_count: int
    included_task_count: int
    included_image_count: int
    ignored_task_count: int
    training_profile: Optional[str] = None
    positive_image_count: int = 0
    negative_image_count: int = 0
    instance_count: int = 0
    invalid_image_count: int = 0
    merged_classes: List[MergedTrainingClassResponse] = Field(default_factory=list)
    included_tasks: List[TrainingTaskIncludedTaskResponse] = Field(default_factory=list)
    ignored_tasks: List[TrainingTaskIgnoredTaskResponse] = Field(default_factory=list)
    selected_label_ids: List[int] = Field(default_factory=list)
    label_set_signature: Optional[str] = None
    class_order_signature: Optional[str] = None
    dataset_category_id: Optional[int] = None
    training_recipe_key: Optional[str] = None
    training_recipe_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    runtime_interface_version: Optional[int] = None
    sample_conditions: Dict[str, List[str]] = Field(default_factory=dict)
    sample_condition_option_counts: Dict[str, Dict[str, int]] = Field(
        default_factory=dict
    )
    branch_key: Optional[str] = None
    selection_contract_sha256: Optional[str] = None
    selection_contract: Optional[Dict[str, object]] = None


class TrainingTaskCreateResponse(BaseModel):
    training_task_id: int
    name: str
    mode: str
    primary_task_id: Optional[int] = None
    latest_job_id: int
    series_id: Optional[int] = None
    variant_id: Optional[int] = None
    branch_key: Optional[str] = None
    selection_contract_sha256: Optional[str] = None
    included_task_count: int
    included_image_count: int
    ignored_task_count: int
    message: str


class SelectedPresetSnapshotResponse(BaseModel):
    slug: str
    name: str


class SelectedLabelItemSnapshotResponse(BaseModel):
    preset_slug: str = ""
    label_value: str = ""
    display_name: str
    selected_order: int


class TrainingTaskDetailResponse(BaseModel):
    training_task_id: int
    name: str
    mode: str
    status: str
    primary_task_id: Optional[int] = None
    primary_task_name: Optional[str] = None
    selected_presets: List[SelectedPresetSnapshotResponse] = Field(default_factory=list)
    selected_label_items: List[SelectedLabelItemSnapshotResponse] = Field(
        default_factory=list
    )
    merged_classes: List[MergedTrainingClassResponse] = Field(default_factory=list)
    source_tasks: List[TrainingTaskIncludedTaskResponse] = Field(default_factory=list)
    ignored_tasks: List[TrainingTaskIgnoredTaskResponse] = Field(default_factory=list)
    selected_label_ids: List[int] = Field(default_factory=list)
    label_set_signature: Optional[str] = None
    class_order_signature: Optional[str] = None
    dataset_category_id: Optional[int] = None
    training_recipe_key: Optional[str] = None
    training_recipe_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    runtime_interface_version: Optional[int] = None
    sample_conditions: Dict[str, List[str]] = Field(default_factory=dict)
    latest_job_id: Optional[int] = None
    latest_job_status: Optional[str] = None
    series_id: Optional[int] = None
    variant_id: Optional[int] = None
    branch_key: Optional[str] = None
    selection_contract_sha256: Optional[str] = None
    selection_contract: Optional[Dict[str, object]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TrainingTaskListRowResponse(BaseModel):
    training_task_id: int
    name: str
    mode: str
    status: str
    primary_task_id: Optional[int] = None
    primary_task_name: Optional[str] = None
    source_task_count: int = 0
    display_subtitle: Optional[str] = None
    latest_job_id: Optional[int] = None
    latest_job_status: Optional[str] = None
    series_id: Optional[int] = None
    variant_id: Optional[int] = None
    branch_key: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
