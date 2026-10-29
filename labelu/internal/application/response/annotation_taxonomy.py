from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class LabelPresetItemResponse(BaseModel):
    id: int
    label_id: Optional[int] = None
    label_key: str
    label_value: str
    color: str
    sort_order: int


class LabelPresetResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: Union[str, None] = None
    training_profile: Union[str, None] = None
    training_profile_config: Union[Dict[str, Any], None] = None
    sort_order: int = 0
    items: List[LabelPresetItemResponse] = Field(default_factory=list)
    label_ids: List[int] = Field(default_factory=list)
    created_at: Union[datetime, None] = None
    updated_at: Union[datetime, None] = None


class GlobalTagOptionResponse(BaseModel):
    id: int
    name_cn: str
    value_en: str
    sort_order: int


class GlobalTagDimensionResponse(BaseModel):
    id: int
    field_key: str
    name_cn: str
    sort_order: int = 0
    options: List[GlobalTagOptionResponse] = Field(default_factory=list)
    created_at: Union[datetime, None] = None
    updated_at: Union[datetime, None] = None


class AnnotationLabelResponse(BaseModel):
    id: int
    label_key: str
    label_value: str
    annotation_kind: str
    color: str
    attributes: List[Dict[str, Any]] = Field(default_factory=list)
    description: Optional[str] = None
    status: str
    version: int
    sort_order: int
    reference_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AnnotationLabelAliasResponse(BaseModel):
    id: int
    source_system: str
    external_label_value: str
    label_id: int
    label_value: str
    status: str
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DatasetCategoryResponse(BaseModel):
    id: int
    category_key: str
    name: str
    description: Optional[str] = None
    sort_order: int
    status: str
    version: int
    reference_count: int = 0
    is_system: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TrainingRecipeResponse(BaseModel):
    id: int
    recipe_key: str
    version: int
    name: str
    job_type: str
    model_family: str
    annotation_kind: str
    component_contract: Dict[str, Any] = Field(default_factory=dict)
    default_hyperparams: Dict[str, Any] = Field(default_factory=dict)
    status: str


class RuntimePublishTemplateResponse(BaseModel):
    id: int
    template_key: str
    name: str
    environment_id: str
    runtime_profile: str
    interface_version: int
    aicm_model_set_id: Optional[str] = None
    expected_model_alg_type: Optional[str] = None
    capability_contract: Dict[str, Any] = Field(default_factory=dict)
    template_sha256: Optional[str] = None
    is_default: bool
    status: str
    version: int


class TaskAnnotationContractResponse(BaseModel):
    id: int
    task_id: int
    revision: int
    annotation_kind: str
    label_ids: List[int] = Field(default_factory=list)
    labels: List[Dict[str, Any]] = Field(default_factory=list)
    label_set_signature: str
    class_order_signature: str
    status: str
    source_preset_id: Optional[int] = None
    dataset_label_contract_id: Optional[int] = None
    label_scope_mode: str = "FULL"
