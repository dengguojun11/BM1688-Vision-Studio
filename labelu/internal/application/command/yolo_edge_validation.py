import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

THRESHOLD_RATIO_UNIT = "ratio_0_1"
THRESHOLD_RATIO_RANGE: tuple[float, float] = (0.0, 1.0)
EDGE_THRESHOLD_RANGES: dict[str, tuple[float, float]] = {
    "conf_thres": (0.15, 0.60),
    "nms_thres": (0.30, 0.80),
    "match_iou_thres": (0.40, 0.70),
}
EDGE_THRESHOLD_METADATA_FIELDS = {
    "threshold_unit",
    "unit",
    "source",
    "reason",
    "confidence_level",
    "provider_key",
    "requested",
    "defaulted_fields",
}
EDGE_THRESHOLD_ALLOWED_FIELDS = (
    set(EDGE_THRESHOLD_RANGES) | EDGE_THRESHOLD_METADATA_FIELDS
)


def _ratio_value(
    value: Any, *, field_name: str, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool):
        raise ValueError(
            f"{field_name} 必须是 {THRESHOLD_RATIO_UNIT} 数值，不能是布尔值"
        )
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} 必须是 {THRESHOLD_RATIO_UNIT} 数值，不能传百分数字符串"
        ) from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} 必须是有限数值")
    if normalized < minimum or normalized > maximum:
        raise ValueError(
            f"{field_name} 超出专业范围 {minimum:.2f}~{maximum:.2f}；"
            f"接口单位为 {THRESHOLD_RATIO_UNIT}，不能传 35、60 等百分数"
        )
    return normalized


def normalize_edge_threshold_payload(
    value: Any, *, field_name: str = "thresholds"
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    normalized = dict(value)
    unsupported = sorted(
        str(key) for key in normalized if key not in EDGE_THRESHOLD_ALLOWED_FIELDS
    )
    if unsupported:
        raise ValueError(
            f"{field_name} 包含不支持字段：{', '.join(unsupported)}；"
            f"阈值字段仅支持 {', '.join(EDGE_THRESHOLD_RANGES)}"
        )
    explicit_unit = normalized.get("threshold_unit")
    legacy_unit = normalized.get("unit")
    for unit in (explicit_unit, legacy_unit):
        if unit in (None, ""):
            continue
        if str(unit).strip().lower() != THRESHOLD_RATIO_UNIT:
            raise ValueError(
                f"{field_name}.threshold_unit 仅支持 {THRESHOLD_RATIO_UNIT}，不支持百分数"
            )
    normalized["threshold_unit"] = THRESHOLD_RATIO_UNIT
    normalized.pop("unit", None)
    minimum, maximum = THRESHOLD_RATIO_RANGE
    for key in EDGE_THRESHOLD_RANGES:
        if key not in normalized:
            continue
        if normalized[key] is None:
            continue
        normalized[key] = _ratio_value(
            normalized[key],
            field_name=f"{field_name}.{key}",
            minimum=minimum,
            maximum=maximum,
        )
    return normalized


class CreateYoloEdgeValidationJobCommand(BaseModel):
    export_id: int = Field(gt=0)
    task_id: int = Field(gt=0)
    mode: Literal["run_only", "run_and_eval"] = "run_and_eval"
    execution_profile: Literal["quick", "certification"] = "quick"
    sample_ids: list[int] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)
    external_attachments: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    threshold_source: str | None = None
    ai_thresholds: dict[str, Any] | None = None

    @field_validator("thresholds", mode="before")
    @classmethod
    def validate_thresholds(cls, value: Any) -> dict[str, Any]:
        return normalize_edge_threshold_payload(value, field_name="thresholds")

    @field_validator("ai_thresholds", mode="before")
    @classmethod
    def validate_ai_thresholds(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return normalize_edge_threshold_payload(value, field_name="ai_thresholds")


class RecommendEdgeThresholdsCommand(BaseModel):
    export_id: int = Field(gt=0)
    task_id: int = Field(gt=0)
    mode: Literal["run_only", "run_and_eval"] = "run_and_eval"
    sample_ids: list[int] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)


class RefineEdgeThresholdsCommand(BaseModel):
    max_images: int = Field(default=4, ge=1, le=8)
    include_visual_evidence: bool = True
