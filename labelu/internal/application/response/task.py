from datetime import datetime

from typing import List, Union
from pydantic import BaseModel, EmailStr, Field

from labelu.internal.application.response.base import UserResp


class TaskResponse(BaseModel):
    id: Union[int, None] = Field(default=None, description="description: task id")
    project_id: Union[int, None] = Field(default=None, description="7GAI project id")
    name: Union[str, None] = Field(default=None, description="description: task name")
    description: Union[str, None] = Field(
        default=None, description="description: task description"
    )
    tips: Union[str, None] = Field(default=None, description="description: task tips")
    config: Union[str, None] = Field(
        default=None, description="description: task config content"
    )
    media_type: Union[str, None] = Field(
        default=None, description="description: task media type: IMAGE, VIDEO, AUDIO"
    )
    status: Union[str, None] = Field(
        default=None,
        description="description: task status: DRAFT, IMPORTED, CONFIGURED, INPROGRESS, FINISHED",
    )
    created_at: Union[datetime, None] = Field(
        default=None, description="description: task created at time"
    )
    created_by: Union[UserResp, None] = Field(
        default=None, description="description: task created by"
    )
    source_type: str = "internal"
    source_system: Union[str, None] = None
    source_external_task_id: Union[str, None] = None
    source_external_package_id: Union[str, None] = None
    source_integration_id: Union[int, None] = None
    current_annotation_contract_id: Union[int, None] = None
    annotation_contract_revision: Union[int, None] = None
    label_set_signature: Union[str, None] = None
    class_order_signature: Union[str, None] = None
    dataset_category_id: Union[int, None] = None
    dataset_category_key: Union[str, None] = None
    dataset_category_name: Union[str, None] = None
    dataset_category_status: Union[str, None] = None
    dataset_id: Union[int, None] = None
    dataset_label_contract_id: Union[int, None] = None
    task_label_scope_mode: str = "FULL"


class TaskProjectAssignmentResponse(BaseModel):
    task_ids: List[int]
    project_id: int
    updated_count: int


class TaskStatics(BaseModel):
    new: Union[int, None] = Field(
        default=0, description="description: count for task data have not labeled yet"
    )
    done: Union[int, None] = Field(
        default=0, description="description: count for task data already labeled"
    )
    skipped: Union[int, None] = Field(
        default=0, description="description: count for task data skipped"
    )
    abnormal: Union[int, None] = Field(
        default=0,
        description="description: count for task data with abnormal annotations",
    )
    incomplete: Union[int, None] = Field(
        default=0,
        description="description: count for task data with incomplete annotations",
    )


class TaskAnnotationAuditSummary(BaseModel):
    total_samples: Union[int, None] = Field(default=0)
    passed_samples: Union[int, None] = Field(default=0)
    failed_samples_count: Union[int, None] = Field(default=0)
    failed_missing_tags_count: Union[int, None] = Field(default=0)
    failed_missing_rect_count: Union[int, None] = Field(default=0)
    failed_missing_geometry_count: Union[int, None] = Field(default=0)
    failed_incomplete_count: Union[int, None] = Field(default=0)
    failed_geometry_count: Union[int, None] = Field(default=0)


class TaskResponseWithStatics(TaskResponse):
    stats: Union[TaskStatics, None] = None
    training_ready: Union[bool, None] = Field(
        default=None,
        description="IMAGE task: true when every blocking sample has global tags and valid configured geometry",
    )
    annotation_audit: Union[TaskAnnotationAuditSummary, None] = None


class TaskDatasetCategorySummaryItem(BaseModel):
    id: int
    category_key: str
    name: str
    status: str
    count: int = 0


class TaskDatasetCategorySummaryResponse(BaseModel):
    all: int = 0
    categorized: int = 0
    uncategorized: int = 0
    categories: List[TaskDatasetCategorySummaryItem] = Field(default_factory=list)


class TaskDatasetCategoryBatchResponse(BaseModel):
    task_ids: List[int] = Field(default_factory=list)
    dataset_category_id: Union[int, None] = None
    updated_task_count: int = 0
    synced_training_task_count: int = 0
    synced_train_job_count: int = 0
    synced_integration_count: int = 0


class TaskRectPresetSummaryItem(BaseModel):
    slug: str
    name: str
    count: int = 0


class TaskRectPresetSummaryResponse(BaseModel):
    all: int = 0
    presets: List[TaskRectPresetSummaryItem] = Field(default_factory=list)
    uncategorized: int = 0


class TaskImageSearchHit(BaseModel):
    sample_id: int
    task_id: int
    task_name: str
    filename: str
    state: Union[str, None] = None
    url: Union[str, None] = None
    thumbnail_url: Union[str, None] = None


class TaskLabelImageHit(BaseModel):
    sample_id: int
    task_id: int
    task_name: str
    filename: str
    state: Union[str, None] = None
    url: Union[str, None] = None
    thumbnail_url: Union[str, None] = None
    width: Union[float, None] = None
    height: Union[float, None] = None
    annotations: List[dict] = Field(default_factory=list)


class TaskTrainingCandidateResponse(BaseModel):
    task_id: int
    task_name: Union[str, None] = None
    # ``training_ready`` remains the task-level flag: all required samples
    # passed the audit.  A task may still be trainable when this is false
    # because the current snapshot can use a qualified subset.
    training_ready: bool = True
    can_train: bool = False
    training_state: str = "UNAVAILABLE"
    task_status: Union[str, None] = None
    total_sample_count: int = 0
    required_sample_count: int = 0
    done_count: int = 0
    done_sample_count: int = 0
    trainable_sample_count: int = 0
    pending_sample_count: int = 0
    excluded_sample_count: int = 0
    completion_ratio: float = 0.0
    quality_pass_ratio: float = 0.0
    minimum_threshold_met: bool = False
    minimum_trainable_sample_count: int = 1
    training_block_reasons: List[str] = Field(default_factory=list)
    created_at: Union[datetime, None] = None
    resolved_rect_preset_slug: Union[str, None] = None
    match_source: Union[str, None] = Field(
        default=None,
        description="explicit_preset | class_fallback | consistent_pool",
    )
    training_profile: Union[str, None] = None
    positive_image_count: int = 0
    negative_image_count: int = 0
    instance_count: int = 0
    invalid_image_count: int = 0
    contract_id: Union[int, None] = None
    contract_revision: Union[int, None] = None
    label_ids: List[int] = Field(default_factory=list)
    contract_labels: List[dict] = Field(default_factory=list)
    label_set_signature: Union[str, None] = None
    class_order_signature: Union[str, None] = None
    contract_status: Union[str, None] = None
    dataset_category_id: Union[int, None] = None
    dataset_category_name: Union[str, None] = None
    dataset_id: Union[int, None] = None
    dataset_label_contract_id: Union[int, None] = None
    task_label_scope_mode: str = "FULL"


class User(BaseModel):
    id: Union[int, None] = None
    username: Union[EmailStr, None] = None
