from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator


class SelectedPresetInput(BaseModel):
    preset_slug: str = Field(..., min_length=1, max_length=128)

    @validator("preset_slug", pre=True)
    def _normalize_slug(cls, v):
        return str(v or "").strip()


class SelectedLabelItemInput(BaseModel):
    preset_slug: str = Field(..., min_length=1, max_length=128)
    label_value: str = Field(..., min_length=1, max_length=256)

    @validator("preset_slug", "label_value", pre=True)
    def _normalize_values(cls, v):
        return str(v or "").strip()


class CheckTrainingTaskNameCommand(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)

    @validator("name", pre=True)
    def _normalize_name(cls, v):
        return str(v or "").strip()


class PreviewLabelDimensionTrainingTaskCommand(BaseModel):
    selected_presets: List[SelectedPresetInput] = Field(default_factory=list)
    selected_label_items: List[SelectedLabelItemInput] = Field(default_factory=list)
    selected_task_ids: Optional[List[int]] = Field(default=None)
    dataset_id: Optional[int] = Field(default=None, gt=0)
    # ``dataset_id`` remains the legacy primary-dataset field.  New training
    # requests may select several logical datasets; the server validates that
    # their label contracts are compatible before building one immutable
    # snapshot.
    dataset_ids: List[int] = Field(default_factory=list)
    label_ids: List[int] = Field(default_factory=list)
    dataset_category_id: Optional[int] = Field(default=None, gt=0)
    dataset_category_ids: List[int] = Field(default_factory=list)
    selected_task_label_ids: Optional[Dict[str, List[int]]] = Field(default=None)
    training_recipe_key: str = Field(default="yolov11_detection", max_length=128)
    training_recipe_version: int = Field(default=1, ge=1)
    plate_training_target: Optional[Literal["plate_detection", "plate_recognition"]] = (
        Field(
            default=None,
            description=(
                "车牌标注任务的训练目标。plate_detection=仅训练定位与四角点；"
                "plate_recognition=兼容原四组件识别。为空时按车牌 profile 默认值。"
            ),
        )
    )
    runtime_profile: str = Field(default="yolov11_detection", max_length=128)
    runtime_interface_version: int = Field(default=1, ge=1)
    sample_conditions: Dict[str, List[str]] = Field(
        default_factory=dict,
        description=(
            "Optional global tag filters. Values within one dimension use OR; "
            "different dimensions use AND. Filters only limit training samples."
        ),
    )
    # Model-series branch identity is optional for legacy task creation.  The
    # service computes a canonical contract/hash when omitted.
    series_id: Optional[int] = Field(default=None, gt=0)
    variant_id: Optional[int] = Field(default=None, gt=0)
    series_name: Optional[str] = Field(default=None, max_length=128)
    variant_name: Optional[str] = Field(default=None, max_length=128)
    model_family: Optional[str] = Field(default=None, max_length=128)
    parent_model_version_id: Optional[int] = Field(default=None, gt=0)
    parent_training_job_id: Optional[int] = Field(default=None, gt=0)
    branch_key: Optional[str] = Field(default=None, min_length=1, max_length=64)
    inheritance_mode: Optional[str] = Field(default=None, max_length=32)
    selection_contract: Optional[Dict[str, Any]] = None

    @validator("label_ids")
    def _normalize_label_ids(cls, v):
        result = []
        for value in v:
            label_id = int(value)
            if label_id <= 0:
                raise ValueError("label_ids 只能包含正整数")
            if label_id in result:
                raise ValueError("label_ids 不能重复；数组顺序就是 class ID 顺序")
            result.append(label_id)
        return result

    @validator("selected_task_ids")
    def _normalize_selected_task_ids(cls, v):
        if v is None:
            return None
        out = []
        for value in v:
            task_id = int(value)
            if task_id <= 0:
                raise ValueError("selected_task_ids 只能包含正整数")
            if task_id not in out:
                out.append(task_id)
        return out

    @validator("dataset_category_ids")
    def _normalize_dataset_category_ids(cls, v):
        out = []
        for value in v:
            category_id = int(value)
            if category_id <= 0:
                raise ValueError("dataset_category_ids 只能包含正整数")
            if category_id not in out:
                out.append(category_id)
        return out

    @validator("dataset_ids", pre=True)
    def _normalize_dataset_ids(cls, v):
        if v is None:
            return []
        if not isinstance(v, (list, tuple, set)):
            raise ValueError("dataset_ids 必须是正整数列表")
        out = []
        for value in v:
            dataset_id = int(value)
            if dataset_id <= 0:
                raise ValueError("dataset_ids 只能包含正整数")
            if dataset_id not in out:
                out.append(dataset_id)
        return out

    @validator("selected_task_label_ids")
    def _normalize_selected_task_label_ids(cls, v):
        if v is None:
            return None
        out = {}
        for task_id, label_ids in v.items():
            normalized_task_id = int(task_id)
            if normalized_task_id <= 0:
                raise ValueError("selected_task_label_ids 的任务 ID 无效")
            normalized_labels = []
            for label_id in label_ids or []:
                value = int(label_id)
                if value <= 0:
                    raise ValueError("selected_task_label_ids 的标签 ID 无效")
                if value not in normalized_labels:
                    normalized_labels.append(value)
            if normalized_labels:
                out[str(normalized_task_id)] = normalized_labels
        return out

    @validator("sample_conditions")
    def _normalize_sample_conditions(cls, value):
        if not value:
            return {}
        if len(value) > 32:
            raise ValueError("sample_conditions 最多包含 32 个维度")
        normalized = {}
        for raw_key, raw_values in value.items():
            key = str(raw_key or "").strip()
            if not key or len(key) > 64:
                raise ValueError("sample_conditions 包含无效维度")
            values = []
            for raw_value in raw_values or []:
                item = str(raw_value or "").strip()
                if not item or len(item) > 128:
                    raise ValueError(f"sample_conditions.{key} 包含无效选项")
                if item not in values:
                    values.append(item)
            if values:
                normalized[key] = values
        return normalized


class CreateLabelDimensionTrainingTaskCommand(PreviewLabelDimensionTrainingTaskCommand):
    name: str = Field(..., min_length=1, max_length=128)
    model_weights: str = Field(default="yolo11n.pt", max_length=256)
    weights_source: Optional[str] = Field(default="preset", max_length=32)
    weights_ref_id: Optional[int] = Field(default=None, gt=0)
    epochs: int = Field(default=50, ge=1, le=3000)
    imgsz: int = Field(default=640, ge=320, le=1280)
    hyperparams: Dict[str, Any] = Field(default_factory=dict)
    iteration_source_job_id: Optional[int] = Field(default=None, gt=0)
    @validator("name", "model_weights", "weights_source", pre=True)
    def _normalize_strings(cls, v):
        if v is None:
            return v
        return str(v).strip()
