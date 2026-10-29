from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class YoloQualityPolicyResponse(BaseModel):
    policy_name: str
    scene_name: str
    rect_preset_slug: str
    label_ids: List[int] = Field(default_factory=list)
    model_family: str = "yolov11"
    annotation_kind: str = "bbox"
    label_set_signature: Optional[str] = None
    status: str
    source: str
    version: int
    is_active: bool = False
    source_summary: Dict[str, Any] = Field(default_factory=dict)
    training_quality: Dict[str, Any] = Field(default_factory=dict)
    generalization_quality: Dict[str, Any] = Field(default_factory=dict)
    edge_usability: Dict[str, Any] = Field(default_factory=dict)
    release_readiness: Dict[str, Any] = Field(default_factory=dict)
    key_classes: List[Dict[str, Any]] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


class YoloQualityPoliciesBundleResponse(BaseModel):
    active_policy_name: Optional[str] = None
    active_policy_names: Dict[str, str] = Field(default_factory=dict)
    policies: List[YoloQualityPolicyResponse] = Field(default_factory=list)
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="当前配置主读写来源",
    )
    runtime_file: str = Field(default="", description="兼容同步文件路径（非主读写来源）")


class YoloQualityPolicyRecommendationResponse(BaseModel):
    recommendation: YoloQualityPolicyResponse
    default_recommendation: YoloQualityPolicyResponse
    fallback_used: bool = False
    provider_key: Optional[str] = None
    fallback_reason: str = ""
