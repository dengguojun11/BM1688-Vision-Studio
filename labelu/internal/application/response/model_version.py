from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ModelArtifactResponse(BaseModel):
    id: int
    model_version_id: int
    artifact_type: str
    relative_path: str
    sha256: str
    size_bytes: int | None = None
    chip_type: str | None = None
    precision: str | None = None
    status: str
    metadata: dict[str, Any] | None = None
    created_by: int
    created_at: datetime | None = None


class ModelQualityEvidenceResponse(BaseModel):
    id: int
    model_version_id: int
    gate_type: str
    revision: int
    status: str
    metrics: dict[str, Any] | None = None
    evidence_uri: str | None = None
    evidence_sha256: str | None = None
    evaluator: str | None = None
    reason: str | None = None
    evaluated_at: datetime | None = None
    created_by: int
    created_at: datetime | None = None


class ModelVersionResponse(BaseModel):
    id: int
    algorithm_id: int
    # The owning annotation task is derived from the immutable training job
    # when available.  Keep it optional for legacy/imported model versions
    # that do not have a training-job relationship.
    task_id: int | None = None
    algorithm_key: str | None = None
    dataset_id: int | None = None
    dataset_version_id: int | None = None
    dataset_version_no: int | None = None
    series_id: int | None = None
    variant_id: int | None = None
    series_name: str | None = None
    variant_name: str | None = None
    model_family: str | None = None
    branch_key: str | None = None
    selection_contract_sha256: str | None = None
    selection_contract: dict[str, Any] | None = None
    inheritance_mode: str | None = None
    parent_training_job_id: int | None = None
    training_job_id: int | None = None
    training_model_weights: str | None = None
    training_recipe_key: str | None = None
    training_recipe_version: int | None = None
    label_set_signature: str | None = None
    class_order_signature: str | None = None
    bm1688_export_id: int | None = None
    parent_model_version_id: int | None = None
    published_catalog_id: int | None = None
    model_key: str
    # Branch-local sequence (v1/v2/v3). ``version_no`` remains the legacy
    # algorithm-global sequence and is kept for compatibility.
    branch_version_no: int | None = None
    version_no: int
    display_name: str
    status: str
    chip_type: str | None = None
    precision: str | None = None
    quality_gate_status: str | None = None
    input_contract: dict[str, Any] | None = None
    output_contract: dict[str, Any] | None = None
    preprocess_contract: dict[str, Any] | None = None
    postprocess_contract: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    artifacts: list[ModelArtifactResponse] = Field(default_factory=list)
    quality_evidence: list[ModelQualityEvidenceResponse] = Field(default_factory=list)
    quality_gate: dict[str, str] = Field(default_factory=dict)
    # 边端检查结果来自 yolo_edge_validation_job。它是模型中心的只读投影，
    # 不会伪造或替代模型质量证据，避免检查完成后列表仍显示旧状态。
    edge_validation_status: dict[str, Any] | None = None
    # 当前边端版本与同系列上一版本的真实边端推理结果对比。
    # 保存在版本响应内，供模型中心列表直接展示，避免前端自行猜测基线。
    edge_version_comparison: dict[str, Any] | None = None
    edge_version_improvement_status: str | None = None
    created_by: int
    published_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None
    retired_at: datetime | None = None


class ModelFamilyResponse(BaseModel):
    """A model series with its complete, immutable version history."""

    family_key: str
    display_name: str
    task_id: int
    algorithm_id: int
    latest_version_id: int
    version_count: int
    published_version_id: int | None = None
    updated_at: datetime | None = None
    versions: list[ModelVersionResponse] = Field(default_factory=list)
