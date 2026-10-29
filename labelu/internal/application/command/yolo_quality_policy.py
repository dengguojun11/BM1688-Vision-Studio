from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RecommendYoloQualityPolicyCommand(BaseModel):
    scene_name: str = Field(..., min_length=1, max_length=128)
    rect_preset_slug: str = Field(default="", max_length=128)
    label_ids: List[int] = Field(default_factory=list)
    model_family: str = Field(default="yolov11", max_length=64)
    annotation_kind: str = Field(default="bbox", max_length=32)
    label_set_signature: Optional[str] = Field(default=None, min_length=64, max_length=64)
    benchmark_name: Optional[str] = Field(default=None, max_length=256)
    benchmark_image_count: Optional[int] = Field(default=None, ge=0)
    benchmark_box_count: Optional[int] = Field(default=None, ge=0)
    include_historical_training: bool = True
    include_edge_validation_history: bool = True
    include_ai_eval_reports: bool = True
    business_goal: Optional[str] = Field(default=None, max_length=200)
    key_classes: List[str] = Field(default_factory=list)
    business_constraints: Dict[str, Any] = Field(default_factory=dict)


class CreateOrUpdateYoloQualityPolicyCommand(BaseModel):
    policy_name: str = Field(..., min_length=1, max_length=128)
    scene_name: str = Field(..., min_length=1, max_length=128)
    rect_preset_slug: str = Field(default="", max_length=128)
    label_ids: List[int] = Field(default_factory=list)
    model_family: str = Field(default="yolov11", max_length=64)
    annotation_kind: str = Field(default="bbox", max_length=32)
    label_set_signature: Optional[str] = Field(default=None, min_length=64, max_length=64)
    status: str = Field(default="draft", max_length=32)
    source: str = Field(default="manual", max_length=32)
    version: int = Field(default=1, ge=1)
    source_summary: Dict[str, Any] = Field(default_factory=dict)
    training_quality: Dict[str, Any] = Field(default_factory=dict)
    generalization_quality: Dict[str, Any] = Field(default_factory=dict)
    edge_usability: Dict[str, Any] = Field(default_factory=dict)
    release_readiness: Dict[str, Any] = Field(default_factory=dict)
    key_classes: List[Dict[str, Any]] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class ActivateYoloQualityPolicyCommand(BaseModel):
    policy_name: str = Field(..., min_length=1, max_length=128)
