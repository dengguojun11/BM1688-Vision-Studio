from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from labelu.internal.application.command.yolo_edge_validation import (
    EDGE_THRESHOLD_RANGES,
    THRESHOLD_RATIO_UNIT,
    normalize_edge_threshold_payload,
)


BUSINESS_THRESHOLD_FIELDS = (
    "edge_low_threshold",
    "edge_high_threshold",
    "flink_filter_threshold",
)
BUSINESS_THRESHOLD_METADATA_FIELDS = {
    "threshold_unit",
    "unit",
    "source",
    "reason",
    "confidence_level",
    "provider_key",
    "requested",
    "defaulted_fields",
}


def validate_ratio_threshold_units(
    value: dict[str, Any],
    *,
    field_name: str,
) -> bool:
    has_explicit_unit = False
    for key in ("threshold_unit", "unit"):
        unit = value.get(key)
        if unit in (None, ""):
            continue
        has_explicit_unit = True
        if str(unit).strip().lower() != THRESHOLD_RATIO_UNIT:
            raise ValueError(
                f"{field_name}.{key} 仅支持 {THRESHOLD_RATIO_UNIT}，不支持百分数"
            )
    return has_explicit_unit


def normalize_business_threshold_payload(
    value: Any,
    *,
    field_name: str = "experiment.current_thresholds",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    normalized = dict(value)
    allowed_fields = set(BUSINESS_THRESHOLD_FIELDS) | BUSINESS_THRESHOLD_METADATA_FIELDS
    unsupported = sorted(str(key) for key in normalized if key not in allowed_fields)
    if unsupported:
        raise ValueError(
            f"{field_name} 包含不支持字段：{', '.join(unsupported)}；"
            f"阈值字段仅支持 {', '.join(BUSINESS_THRESHOLD_FIELDS)}"
        )
    validate_ratio_threshold_units(normalized, field_name=field_name)
    normalized.pop("threshold_unit", None)
    normalized.pop("unit", None)
    for key in BUSINESS_THRESHOLD_FIELDS:
        if key not in normalized:
            continue
        raw = normalized[key]
        if raw is None:
            continue
        if isinstance(raw, bool):
            raise ValueError(f"{field_name}.{key} 必须是 {THRESHOLD_RATIO_UNIT} 数值")
        try:
            ratio = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field_name}.{key} 必须是 {THRESHOLD_RATIO_UNIT} 数值，不能传百分数字符串"
            ) from exc
        if not math.isfinite(ratio) or ratio < 0.0 or ratio > 1.0:
            raise ValueError(
                f"{field_name}.{key} 必须在 0~1；接口单位为 {THRESHOLD_RATIO_UNIT}，"
                "不能传 35、60 等百分数"
            )
        normalized[key] = ratio
    return normalized


THRESHOLD_SECTION_VALUE_KEYS = (
    "requested",
    "current",
    "recommended",
    "effective",
)
THRESHOLD_SECTION_METADATA_FIELDS = {
    "threshold_unit",
    "unit",
    "source",
    "reason",
    "confidence_level",
    "provider_key",
    "defaulted_fields",
    "goal",
    "recommendation_source",
    "mapping_detail",
}


def normalize_threshold_section_payload(
    value: Any,
    *,
    field_name: str,
    threshold_kind: Literal["business", "validation"],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    normalized = dict(value)
    validate_ratio_threshold_units(normalized, field_name=field_name)

    normalizer = (
        normalize_business_threshold_payload
        if threshold_kind == "business"
        else normalize_edge_threshold_payload
    )
    direct_fields = (
        BUSINESS_THRESHOLD_FIELDS
        if threshold_kind == "business"
        else tuple(EDGE_THRESHOLD_RANGES)
    )
    allowed_fields = (
        set(THRESHOLD_SECTION_VALUE_KEYS)
        | THRESHOLD_SECTION_METADATA_FIELDS
        | set(direct_fields)
    )
    unsupported = sorted(str(key) for key in normalized if key not in allowed_fields)
    if unsupported:
        raise ValueError(
            f"{field_name} 包含不支持字段：{', '.join(unsupported)}"
        )
    for key in THRESHOLD_SECTION_VALUE_KEYS:
        if key not in normalized or normalized[key] is None:
            continue
        normalized[key] = normalizer(
            normalized[key],
            field_name=f"{field_name}.{key}",
        )

    direct_payload = {
        key: normalized[key]
        for key in direct_fields
        if key in normalized
    }
    if direct_payload:
        normalized.update(
            normalizer(direct_payload, field_name=field_name)
        )

    normalized["unit"] = THRESHOLD_RATIO_UNIT
    normalized.pop("threshold_unit", None)
    return normalized


def normalize_strategy_experiment_thresholds(
    experiment: dict[str, Any],
    *,
    task_type: str,
) -> dict[str, Any]:
    normalized = dict(experiment or {})
    has_threshold_contract = task_type == "threshold_experiment" or any(
        key in normalized
        for key in ("current_thresholds", "edge_validation_thresholds", "thresholds")
    )
    validate_ratio_threshold_units(normalized, field_name="experiment")
    if has_threshold_contract:
        normalized["threshold_unit"] = THRESHOLD_RATIO_UNIT
        normalized.pop("unit", None)

    current = normalized.get("current_thresholds")
    if current is not None:
        normalized["current_thresholds"] = normalize_business_threshold_payload(current)

    for key in ("edge_validation_thresholds", "thresholds"):
        if key in normalized:
            normalized[key] = normalize_edge_threshold_payload(
                normalized[key],
                field_name=f"experiment.{key}",
            )
    return normalized


class StrategyEvolutionTargetPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_type: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    model_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("modelId", "model_id"),
        serialization_alias="modelId",
    )
    scene_type: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)


class StrategyEvolutionDataWindowPayload(BaseModel):
    start_time: str = Field(min_length=1, max_length=32)
    end_time: str = Field(min_length=1, max_length=32)


class CreateStrategyEvolutionTaskCommand(BaseModel):
    schema_version: str = Field(default="v1", min_length=1, max_length=32)
    external_task_id: str = Field(min_length=1, max_length=128)
    task_name: str = Field(min_length=1, max_length=128)
    task_type: Literal["threshold_experiment", "model_capability"]
    source_package_id: str = Field(default="", max_length=128)
    target: StrategyEvolutionTargetPayload
    data_window: StrategyEvolutionDataWindowPayload
    callback_url: AnyHttpUrl | None = Field(default=None, max_length=1024)
    experiment: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_project_scope(cls, value: Any) -> Any:
        """Keep the public strategy-ingestion contract project-free.

        ``project_id`` may still exist on historical database rows while they
        are being migrated, but it must not be accepted on a new request.  A
        targeted validation error is safer than silently ignoring a caller's
        ownership field and accidentally creating a second scope.
        """
        if isinstance(value, dict) and any(
            key in value for key in ("project_id", "projectId")
        ):
            raise ValueError("策略演化新请求不支持 project_id")
        return value

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return None
        if value.username or value.password:
            raise ValueError("callback_url 不允许包含用户名或密码")
        if value.fragment:
            raise ValueError("callback_url 不允许包含 URL fragment")
        return value

    @model_validator(mode="after")
    def validate_threshold_contract(self):
        self.experiment = normalize_strategy_experiment_thresholds(
            self.experiment,
            task_type=self.task_type,
        )
        return self


class RetryStrategyEvolutionCallbackCommand(BaseModel):
    callback_url: AnyHttpUrl | None = Field(default=None, max_length=1024)

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return None
        if value.username or value.password:
            raise ValueError("callback_url 不允许包含用户名或密码")
        if value.fragment:
            raise ValueError("callback_url 不允许包含 URL fragment")
        return value


class StrategyEvolutionPlatformCallbackCommand(BaseModel):
    schema_version: str = Field(default="v1", min_length=1, max_length=32)
    callback_event_id: str = Field(default="", max_length=128)
    external_task_id: str = Field(min_length=1, max_length=128)
    task_type: Literal["threshold_experiment", "model_capability"]
    status: str = Field(min_length=1, max_length=32)
    progress: dict[str, Any] | None = None
    finished_at: str | None = Field(default=None, max_length=64)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_event_id_alias(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and not value.get("callback_event_id")
            and value.get("event_id")
        ):
            value = dict(value)
            value["callback_event_id"] = value.get("event_id")
        return value

    @model_validator(mode="after")
    def validate_result_threshold_contract(self):
        if not isinstance(self.result, dict):
            return self
        normalized_result = dict(self.result)
        has_threshold_contract = validate_ratio_threshold_units(
            normalized_result,
            field_name="result",
        )
        for key in ("current_thresholds", "recommended_thresholds"):
            if key not in normalized_result:
                continue
            if normalized_result[key] is None:
                has_threshold_contract = True
                continue
            normalized_result[key] = normalize_business_threshold_payload(
                normalized_result[key],
                field_name=f"result.{key}",
            )
            has_threshold_contract = True
        for key in ("edge_validation_thresholds",):
            if key not in normalized_result:
                continue
            if normalized_result[key] is None:
                has_threshold_contract = True
                continue
            normalized_result[key] = normalize_edge_threshold_payload(
                normalized_result[key],
                field_name=f"result.{key}",
            )
            has_threshold_contract = True
        if "business_thresholds" in normalized_result:
            if normalized_result["business_thresholds"] is None:
                has_threshold_contract = True
            else:
                normalized_result["business_thresholds"] = (
                    normalize_threshold_section_payload(
                        normalized_result["business_thresholds"],
                        field_name="result.business_thresholds",
                        threshold_kind="business",
                    )
                )
                has_threshold_contract = True
        if "validation_thresholds" in normalized_result:
            validation_payload = normalized_result["validation_thresholds"]
            if validation_payload is None:
                has_threshold_contract = True
            elif isinstance(validation_payload, dict) and any(
                key in validation_payload for key in THRESHOLD_SECTION_VALUE_KEYS
            ):
                normalized_result["validation_thresholds"] = (
                    normalize_threshold_section_payload(
                        validation_payload,
                        field_name="result.validation_thresholds",
                        threshold_kind="validation",
                    )
                )
                has_threshold_contract = True
            else:
                normalized_result["validation_thresholds"] = (
                    normalize_edge_threshold_payload(
                        validation_payload,
                        field_name="result.validation_thresholds",
                    )
                )
                has_threshold_contract = True
        if has_threshold_contract:
            normalized_result["threshold_unit"] = THRESHOLD_RATIO_UNIT
            normalized_result.pop("unit", None)
        self.result = normalized_result
        return self
