from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union, Literal

from pydantic import BaseModel, Field, root_validator, validator

_SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_label_preset_slug(slug: str) -> str:
    """IllegalParking → illegalParking，与迁移种子及 MySQL 不区分大小写唯一索引一致。"""
    s = (slug or "").strip()
    if len(s) >= 2 and s[0].isupper() and (s[1].islower() or s[1].isdigit()):
        return s[0].lower() + s[1:]
    return s


class LabelPresetItemInput(BaseModel):
    label_key: str = Field(max_length=128)
    label_value: str = Field(max_length=128)
    color: str = Field(max_length=32, description="如 #RRGGBB")
    annotation_kind: str = Field(default="bbox", max_length=32)
    sort_order: int = Field(default=0)

    @validator("label_key", "label_value", "color", "annotation_kind", pre=True)
    def _strip(cls, v):
        if v is None:
            return v
        return str(v).strip()

    @validator("label_key")
    def _key_nonempty(cls, v):
        if not v:
            raise ValueError("label_key（中文展示）不能为空")
        return v

    @validator("label_value")
    def _value_nonempty(cls, v):
        if not v:
            raise ValueError("label_value（英文）不能为空")
        return v

    @validator("color")
    def _color_hex(cls, v):
        if not v:
            return "#1677ff"
        if not _COLOR_RE.match(v):
            raise ValueError("color 须为 #RRGGBB 六位十六进制色值")
        return v

    @validator("annotation_kind")
    def _annotation_kind_supported(cls, v):
        if v not in {"bbox", "polygon"}:
            raise ValueError("annotation_kind 仅支持 bbox 或 polygon")
        return v


class CreateLabelPresetCommand(BaseModel):
    slug: str = Field(max_length=64)
    name: str = Field(max_length=128)
    description: Union[str, None] = Field(default=None, max_length=512)
    sort_order: int = Field(default=0)
    training_profile: Union[str, None] = Field(default=None, max_length=64)
    training_profile_config: Union[Dict[str, Any], None] = None
    items: List[LabelPresetItemInput] = Field(default_factory=list)
    label_ids: List[int] = Field(default_factory=list)

    @validator("slug", "name", pre=True)
    def _strip_text(cls, v):
        if v is None:
            return v
        return str(v).strip()

    @validator("slug")
    def _slug_format(cls, v):
        if not v:
            raise ValueError("slug 不能为空")
        v = normalize_label_preset_slug(v)
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug 须为英文字母开头的标识（驼峰或下划线），如 illegalParking"
            )
        return v

    @validator("name")
    def _name_nonempty(cls, v):
        if not v:
            raise ValueError("名称 name 不能为空")
        return v

    @root_validator(skip_on_failure=True)
    def _labels_nonempty(cls, values):
        if not values.get("items") and not values.get("label_ids"):
            raise ValueError("items 或 label_ids 至少提供一项")
        return values

    @validator("description", pre=True)
    def _desc_empty_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @validator("training_profile", pre=True)
    def _profile_empty_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @validator("training_profile_config")
    def _validate_profile_config(cls, v, values):
        profile = values.get("training_profile")
        if not profile:
            if v is not None:
                raise ValueError(
                    "training_profile_config 需要同时指定 training_profile"
                )
            return None
        if profile not in {"plate_recognition_v1", "plate_detection_v1"}:
            raise ValueError(f"不支持的 training_profile: {profile}")
        from labelu.internal.common.plate_recognition_profile import (
            parse_profile_config,
        )

        return parse_profile_config(v, profile=profile)


class UpdateLabelPresetCommand(BaseModel):
    name: Union[str, None] = Field(default=None, max_length=128)
    description: Union[str, None] = Field(default=None, max_length=512)
    sort_order: Union[int, None] = None
    training_profile: Union[str, None] = Field(default=None, max_length=64)
    training_profile_config: Union[Dict[str, Any], None] = None
    items: Union[List[LabelPresetItemInput], None] = None
    label_ids: Union[List[int], None] = None

    @validator("name", pre=True)
    def _strip_name(cls, v):
        if v is None:
            return None
        return str(v).strip()

    @validator("name")
    def _name_nonempty(cls, v):
        if v is not None and not v:
            raise ValueError("名称 name 不能为空")
        return v

    @validator("description", pre=True)
    def _desc_empty_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @validator("items")
    def _items_nonempty(cls, v):
        if v is not None and len(v) == 0:
            raise ValueError("至少一行标签（中文展示 + 英文值）")
        return v

    @validator("training_profile", pre=True)
    def _normalize_training_profile(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if s and s not in {"plate_recognition_v1", "plate_detection_v1"}:
            raise ValueError(f"不支持的 training_profile: {s}")
        return s or None

    @validator("training_profile_config")
    def _validate_training_profile_config(cls, v, values):
        if v is None:
            return None
        from labelu.internal.common.plate_recognition_profile import (
            parse_profile_config,
        )

        profile = values.get("training_profile")
        if not profile:
            raise ValueError(
                "training_profile_config 需要同时指定 training_profile"
            )
        if profile not in {"plate_recognition_v1", "plate_detection_v1"}:
            raise ValueError(f"不支持的 training_profile: {profile}")
        return parse_profile_config(v, profile=profile)


class GlobalTagOptionInput(BaseModel):
    name_cn: str = Field(max_length=128)
    value_en: str = Field(max_length=128)
    sort_order: int = Field(default=0)


class CreateGlobalTagDimensionCommand(BaseModel):
    field_key: str = Field(max_length=64)
    name_cn: str = Field(max_length=128)
    sort_order: int = Field(default=0)
    options: List[GlobalTagOptionInput] = Field(default_factory=list)


class UpdateGlobalTagDimensionCommand(BaseModel):
    name_cn: Union[str, None] = Field(default=None, max_length=128)
    sort_order: Union[int, None] = None
    options: Union[List[GlobalTagOptionInput], None] = None


class ApplyLabelPresetCommand(BaseModel):
    preset_id: int = Field(gt=0)


class ApplyTaskLabelsCommand(BaseModel):
    label_ids: List[int] = Field(min_items=1)
    expected_contract_revision: Optional[int] = Field(default=None, ge=1)
    config: Optional[str] = None
    tool_name: Optional[str] = None
    scope_mode: Optional[Literal["FULL", "SUBSET"]] = Field(default=None)


class AnnotationLabelAttributeOptionInput(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=128)
    isDefault: bool = False

    @validator("key", "value", pre=True)
    def _strip_option_text(cls, value):
        return str(value or "").strip()


class AnnotationLabelAttributeInput(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=128)
    type: str
    required: bool = False
    options: List[AnnotationLabelAttributeOptionInput] = Field(default_factory=list)
    maxLength: Optional[int] = Field(default=None, ge=1, le=100000)
    stringType: Optional[str] = None
    defaultValue: Optional[str] = Field(default=None, max_length=100000)
    regexp: Optional[str] = Field(default=None, max_length=2000)

    @validator("key", "value", pre=True)
    def _strip_attribute_text(cls, value):
        return str(value or "").strip()

    @validator("type")
    def _attribute_type(cls, value):
        if value not in {"enum", "array", "string"}:
            raise ValueError("type must be enum, array, or string")
        return value

    @root_validator(skip_on_failure=True)
    def _validate_attribute_shape(cls, values):
        attribute_type = values.get("type")
        options = values.get("options") or []
        if attribute_type in {"enum", "array"}:
            if not options:
                raise ValueError("an enumerable attribute requires at least one option")
            option_values = [item.value for item in options]
            if len(option_values) != len(set(option_values)):
                raise ValueError("option values must be unique within an attribute")
            if attribute_type == "enum" and sum(item.isDefault for item in options) > 1:
                raise ValueError(
                    "a single-select attribute allows only one default option"
                )
        else:
            string_type = values.get("stringType") or "text"
            if string_type not in {"text", "number", "order", "regexp", "english"}:
                raise ValueError("unsupported stringType")
            values["stringType"] = string_type
            values["options"] = []
            max_length = values.get("maxLength")
            default_value = values.get("defaultValue") or ""
            if max_length is not None and len(default_value) > max_length:
                raise ValueError("defaultValue exceeds maxLength")
            if string_type == "regexp" and not (values.get("regexp") or "").strip():
                raise ValueError("regexp is required for custom format")
        return values


def _validate_unique_attribute_values(attributes):
    values = [item.value for item in attributes or []]
    if len(values) != len(set(values)):
        raise ValueError("attribute values must be unique within a label")
    return attributes


class CreateAnnotationLabelCommand(BaseModel):
    label_key: str = Field(min_length=1, max_length=128)
    label_value: str = Field(min_length=1, max_length=128)
    annotation_kind: str = Field(default="bbox", max_length=32)
    color: Optional[str] = Field(default=None, max_length=32)
    attributes: List[AnnotationLabelAttributeInput] = Field(default_factory=list)
    description: Optional[str] = Field(default=None, max_length=512)
    sort_order: int = 0
    change_reason: Optional[str] = Field(default=None, max_length=512)

    @validator("label_key", "label_value", "annotation_kind", "color", pre=True)
    def _strip_label_values(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @validator("label_value")
    def _label_value(cls, value):
        if not _SLUG_RE.match(value):
            raise ValueError(
                "label_value 必须以英文字母开头且只能包含字母、数字、下划线"
            )
        return value

    @validator("color")
    def _label_color(cls, value):
        if value is None or value == "":
            return None
        if not _COLOR_RE.match(value):
            raise ValueError("color 必须为 #RRGGBB")
        return value.lower()

    _unique_attribute_values = validator("attributes", allow_reuse=True)(
        _validate_unique_attribute_values
    )

    @validator("annotation_kind")
    def _kind(cls, value):
        normalized = str(value or "").strip().lower()
        if normalized not in {"bbox", "polygon"}:
            raise ValueError("annotation_kind 仅支持 bbox 或 polygon")
        return normalized


class UpdateAnnotationLabelCommand(BaseModel):
    version: int = Field(ge=1)
    label_key: Optional[str] = Field(default=None, min_length=1, max_length=128)
    label_value: Optional[str] = Field(default=None, min_length=1, max_length=128)
    color: Optional[str] = Field(default=None, max_length=32)
    attributes: Optional[List[AnnotationLabelAttributeInput]] = None
    description: Optional[str] = Field(default=None, max_length=512)
    status: Optional[str] = None
    sort_order: Optional[int] = None
    change_reason: Optional[str] = Field(default=None, max_length=512)

    @validator("status")
    def _status(cls, value):
        if value is not None and value not in {"active", "disabled"}:
            raise ValueError("status 必须为 active 或 disabled")
        return value

    @validator("label_key", pre=True)
    def _update_label_key(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("label_key 不能为空")
        return normalized

    @validator("label_value", pre=True)
    def _update_label_value(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("label_value（英文）不能为空")
        if not _SLUG_RE.match(normalized):
            raise ValueError(
                "label_value 必须以英文字母开头且只能包含字母、数字、下划线"
            )
        return normalized

    @validator("color")
    def _update_label_color(cls, value):
        if value is not None and not _COLOR_RE.match(value):
            raise ValueError("color 必须为 #RRGGBB")
        return value.lower() if value is not None else None

    _unique_attribute_values = validator("attributes", allow_reuse=True)(
        _validate_unique_attribute_values
    )


class CreateLabelAliasCommand(BaseModel):
    source_system: str = Field(min_length=1, max_length=64)
    external_label_value: str = Field(min_length=1, max_length=128)
    label_id: int = Field(gt=0)


class UpdateLabelAliasCommand(BaseModel):
    version: int = Field(ge=1)
    label_id: Optional[int] = Field(default=None, gt=0)
    status: Optional[str] = None

    @validator("status")
    def _alias_status(cls, value):
        if value is not None and value not in {"active", "disabled"}:
            raise ValueError("status 必须为 active 或 disabled")
        return value


class CreateDatasetCategoryCommand(BaseModel):
    category_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    sort_order: int = 0

    @validator("category_key")
    def _category_key(cls, value):
        value = str(value or "").strip()
        if not _SLUG_RE.match(value):
            raise ValueError("category_key 必须为英文标识")
        return value


class UpdateDatasetCategoryCommand(BaseModel):
    version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    sort_order: Optional[int] = None
    status: Optional[str] = None

    @validator("status")
    def _category_status(cls, value):
        if value is not None and value not in {"active", "disabled"}:
            raise ValueError("status 必须为 active 或 disabled")
        return value


class UpsertTrainingRecipeCommand(BaseModel):
    recipe_key: str = Field(min_length=1, max_length=128)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=128)
    job_type: str = Field(min_length=1, max_length=64)
    model_family: str = Field(min_length=1, max_length=64)
    annotation_kind: str = Field(default="bbox", max_length=32)
    component_contract: Dict[str, Any] = Field(default_factory=dict)
    default_hyperparams: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"

    @validator("recipe_key")
    def _recipe_key(cls, value):
        value = str(value or "").strip()
        if not _SLUG_RE.match(value):
            raise ValueError("recipe_key 必须为英文标识")
        return value

    @validator("status")
    def _recipe_status(cls, value):
        if value not in {"active", "disabled"}:
            raise ValueError("status 必须为 active 或 disabled")
        return value


class UpsertRuntimeTemplateCommand(BaseModel):
    template_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(default="default", max_length=128)
    runtime_profile: str = Field(min_length=1, max_length=128)
    interface_version: int = Field(default=1, ge=1)
    aicm_model_set_id: Optional[str] = Field(default=None, max_length=128)
    expected_model_alg_type: Optional[str] = Field(default=None, max_length=128)
    capability_contract: Dict[str, Any] = Field(default_factory=dict)
    template_sha256: Optional[str] = Field(default=None, max_length=64)
    is_default: bool = False
    status: str = "active"

    @validator("template_key", "runtime_profile")
    def _runtime_key(cls, value):
        value = str(value or "").strip()
        if not _SLUG_RE.match(value):
            raise ValueError("模板键和 runtime_profile 必须为英文标识")
        return value

    @validator("template_sha256")
    def _template_sha256(cls, value):
        if value is not None and not re.match(r"^[0-9A-Fa-f]{64}$", value):
            raise ValueError("template_sha256 必须为 64 位十六进制 SHA256")
        return value.lower() if value else value

    @validator("aicm_model_set_id", pre=True)
    def _aicm_model_set_id(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if not normalized.isdigit() or int(normalized) <= 0:
            raise ValueError("AICM model_set_id 必须为正整数")
        return normalized

    @validator("status")
    def _runtime_status(cls, value):
        if value not in {"active", "disabled"}:
            raise ValueError("status 必须为 active 或 disabled")
        return value

    @root_validator(skip_on_failure=True)
    def _active_template_requires_model_set(cls, values):
        if values.get("status") == "active" and not values.get("aicm_model_set_id"):
            raise ValueError("启用运行时模板前必须选择 AICM 模型集")
        return values


class UpdateRuntimeTemplateCommand(UpsertRuntimeTemplateCommand):
    version: int = Field(ge=1)


class ApplyGlobalTagsCommand(BaseModel):
    """不提供 dimension_ids 时表示合并当前库中全部未删除维度。"""

    dimension_ids: Optional[List[int]] = Field(default=None)
