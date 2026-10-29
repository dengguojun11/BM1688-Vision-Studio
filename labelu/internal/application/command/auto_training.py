from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator

SeriesType = Literal["yolo_detection", "plate_detection", "plate_recognition"]


class AutoTrainingPlanBaseCommand(BaseModel):
    # 仅保留字段形状以兼容旧客户端的空值序列化；一期创建/预览不再
    # 接受项目维度，历史计划的 project_id 只用于只读审计。
    project_id: Optional[int] = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=128)
    series_type: SeriesType
    dataset_category_id: int = Field(gt=0)
    label_set_signature: str = Field(min_length=64, max_length=64)
    class_order_signature: str = Field(min_length=64, max_length=64)
    config: Dict[str, Any]

    @validator("name", pre=True)
    def _normalize_name(cls, value):
        return str(value or "").strip()

    @validator("project_id")
    def _reject_project_scope(cls, value):
        if value is not None:
            raise ValueError("一期自动训练计划不支持项目维度")
        return None


class PreviewAutoTrainingPlanCommand(AutoTrainingPlanBaseCommand):
    pass


class CreateAutoTrainingPlanCommand(AutoTrainingPlanBaseCommand):
    change_reason: Optional[str] = None


class PatchAutoTrainingPlanCommand(BaseModel):
    expected_version: int = Field(gt=0)
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    series_type: Optional[SeriesType] = None
    dataset_category_id: Optional[int] = Field(default=None, gt=0)
    label_set_signature: Optional[str] = Field(
        default=None, min_length=64, max_length=64
    )
    class_order_signature: Optional[str] = Field(
        default=None, min_length=64, max_length=64
    )
    config: Dict[str, Any]
    change_reason: Optional[str] = None

    @validator("name", pre=True)
    def _normalize_name(cls, value):
        if value is None:
            return None
        return str(value).strip()


class AutoTrainingActionCommand(BaseModel):
    expected_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)
    acknowledge_quality_reached: bool = False

    @validator("reason", pre=True)
    def _normalize_reason(cls, value):
        return str(value or "").strip()


class AutoTrainingApproveCommand(BaseModel):
    expected_version: int = Field(gt=0)
    plan_version_id: Optional[int] = Field(default=None, gt=0)
    expected_status: str = "PENDING_APPROVAL"
    reason: Optional[str] = Field(default=None, max_length=2000)
    activate_after_approval: bool = True

    @validator("reason", pre=True)
    def _normalize_reason(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class AutoTrainingRoundCreateCommand(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class RetryAutoTrainingStageCommand(BaseModel):
    stage: Literal["TRAIN", "BM1688", "EDGE_VALIDATION", "CALLBACK"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_state: str = Field(min_length=1, max_length=48)


class ReasonCommand(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)

    @validator("reason", pre=True)
    def _normalize_reason(cls, value):
        return str(value or "").strip()


class AutoTrainingRuntimeSettingsCommand(BaseModel):
    schema_ready: bool
    observe_only: bool
    outbox_enabled: bool
    pool_enabled: bool
    scheduler_enabled: bool
    train_enabled: bool
    bm1688_enabled: bool
    edge_validation_enabled: bool
    worker_command_enabled: bool = False
    reason: str = Field(min_length=1, max_length=2000)

    @validator("reason", pre=True)
    def _normalize_runtime_reason(cls, value):
        return str(value or "").strip()


class SubmitCurrentQualityPolicyApprovalCommand(BaseModel):
    policy_name: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)

    @validator("policy_name", "reason", pre=True)
    def _normalize_policy_approval_text(cls, value):
        return str(value or "").strip()


class QualityEvidenceCommand(BaseModel):
    expected_state: Literal["PT_EVALUATING"]
    evaluation_run_id: int = Field(gt=0)
    evaluation_request_sha256: str = Field(min_length=64, max_length=64)
    overall_metrics: Dict[str, Any]
    per_label_metrics: Dict[str, Dict[str, Any]]
    plate_component_metrics: Dict[str, Any] = Field(default_factory=dict)
    champion_comparison: Dict[str, Any] = Field(default_factory=dict)
    evidence_manifest_rel_path: str = Field(min_length=1, max_length=512)
    evidence_manifest_sha256: str = Field(min_length=64, max_length=64)

    @validator("overall_metrics")
    def _validate_overall_metrics(cls, value):
        recall = value.get("overall_recall") if isinstance(value, dict) else None
        if isinstance(recall, bool):
            raise ValueError("overall_recall must be a finite number between 0 and 1")
        try:
            parsed = float(recall)
        except (TypeError, ValueError):
            raise ValueError("overall_recall must be a finite number between 0 and 1")
        if not math.isfinite(parsed) or parsed < 0 or parsed > 1:
            raise ValueError("overall_recall must be a finite number between 0 and 1")
        return value

    @validator("per_label_metrics")
    def _validate_per_label_metrics(cls, value):
        for label, metrics in value.items():
            if not str(label).strip() or not isinstance(metrics, dict):
                raise ValueError("per-label metrics must be objects keyed by label")
            for key in ("precision", "recall", "f1", "map50", "map50_95"):
                if key not in metrics or metrics[key] is None:
                    continue
                metric = metrics[key]
                if isinstance(metric, bool):
                    raise ValueError(f"{label}.{key} must be between 0 and 1")
                try:
                    parsed = float(metric)
                except (TypeError, ValueError):
                    raise ValueError(f"{label}.{key} must be between 0 and 1")
                if not math.isfinite(parsed) or parsed < 0 or parsed > 1:
                    raise ValueError(f"{label}.{key} must be between 0 and 1")
        return value

    @validator("evidence_manifest_rel_path", pre=True)
    def _normalize_evidence_path(cls, value):
        normalized = str(value or "").strip().replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("evidence manifest path must be relative")
        return normalized

    @validator("evidence_manifest_sha256")
    def _validate_evidence_sha256(cls, value):
        normalized = str(value or "").strip().lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("evidence manifest sha256 must be hexadecimal")
        return normalized

    @validator("evaluation_request_sha256")
    def _validate_request_sha256(cls, value):
        normalized = str(value or "").strip().lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("evaluation request sha256 must be hexadecimal")
        return normalized


class PublishAutoTrainingRoundCommand(BaseModel):
    expected_state: Literal["PUBLISH_READY", "PUBLISH_FAILED"]
    confirmation: Literal["PUBLISH"]
    reason: str = Field(min_length=1, max_length=2000)
    environment_id: Optional[str] = Field(default=None, max_length=64)
    keep_template_cache: bool = False
    bmodel_precision: Optional[Literal["int8", "fp16", "fp32"]] = None
    bmodel_rel_path: Optional[str] = Field(default=None, max_length=512)

    @validator("reason", pre=True)
    def _normalize_publish_reason(cls, value):
        return str(value or "").strip()


class TrainingReviewCommand(BaseModel):
    annotation_sha256: str = Field(min_length=64, max_length=64)
    review_status: Literal["APPROVED", "REJECTED"]
    comment: Optional[str] = Field(default=None, max_length=2000)
    expected_sample_updated_at: Optional[str] = None


class EvaluationDatasetCommand(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    dataset_kind: Literal[
        "VALIDATION",
        "BENCHMARK",
        "SEALED_ACCEPTANCE",
        "BM1688_CALIBRATION",
        "EDGE_VALIDATION",
    ]
    series_type: SeriesType
    dataset_category_id: int = Field(gt=0)
    label_set_signature: str = Field(min_length=64, max_length=64)
    manifest_rel_path: Optional[str] = Field(default=None, min_length=1, max_length=512)
    manifest_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    sample_count: int = Field(default=0, ge=0)
    grouping_policy: Dict[str, Any]


class AutoGenerateEvaluationDatasetsCommand(BaseModel):
    """Generate the five evaluation datasets from an existing eligible pool."""

    plan_id: Optional[int] = Field(default=None, gt=0)
    series_type: SeriesType
    dataset_category_id: int = Field(gt=0)
    label_set_signature: str = Field(min_length=64, max_length=64)
    grouping_policy: Dict[str, Any] = Field(default_factory=dict)
    name_prefix: Optional[str] = Field(default=None, min_length=1, max_length=96)


class PatchEvaluationDatasetCommand(BaseModel):
    manifest_rel_path: Optional[str] = Field(default=None, min_length=1, max_length=512)
    manifest_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    sample_count: Optional[int] = Field(default=None, ge=0)
    grouping_policy: Optional[Dict[str, Any]] = None


class EvaluationDatasetSampleCommand(BaseModel):
    task_id: int = Field(gt=0)
    sample_id: int = Field(gt=0)
    split_role: Literal[
        "validation", "benchmark", "sealed", "calibration", "edge_validation", "test"
    ]
    identity_group_key: Optional[str] = Field(default=None, max_length=256)
    capture_group_key: Optional[str] = Field(default=None, max_length=256)

    @validator("identity_group_key", "capture_group_key", pre=True)
    def _normalize_group_key(cls, value):
        normalized = str(value or "").strip()
        return normalized or None


class ReplaceEvaluationDatasetSamplesCommand(BaseModel):
    samples: List[EvaluationDatasetSampleCommand] = Field(min_length=1, max_length=5000)


class EvaluationRunClaimCommand(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=300, ge=30, le=3600)

    @validator("worker_id", pre=True)
    def _normalize_worker_id(cls, value):
        return str(value or "").strip()


class QualityPolicyApprovalCommand(BaseModel):
    policy_version: int = Field(gt=0)
    policy_signature: str = Field(min_length=64, max_length=64)
    scope_signature: str = Field(min_length=64, max_length=64)
    policy_snapshot: Dict[str, Any]
    reason: str = Field(min_length=1, max_length=2000)
