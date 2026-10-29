from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TrainingHistoryItem(BaseModel):
    """一轮 YOLO 训练在 results.csv 中留下的可展示指标。"""

    epoch: int
    time_seconds: float | None = None
    train_box_loss: float | None = None
    train_cls_loss: float | None = None
    train_dfl_loss: float | None = None
    val_box_loss: float | None = None
    val_cls_loss: float | None = None
    val_dfl_loss: float | None = None
    precision: float | None = None
    recall: float | None = None
    map50: float | None = None
    map50_95: float | None = None
    fitness: float | None = None
    learning_rate: float | None = None


class TrainingHistoryResponse(BaseModel):
    """训练详情专用的逐轮历史，不进入训练列表响应。"""

    job_id: int
    available: bool = False
    message: str | None = None
    source: str | None = None
    status: str | None = None
    target_epochs: int | None = None
    current_epoch: int | None = None
    best_epoch: int | None = None
    latest: TrainingHistoryItem | None = None
    history: list[TrainingHistoryItem] = Field(default_factory=list)


class TrainingVersionComparisonMetric(BaseModel):
    key: str
    label: str
    previous: float | None = None
    current: float | None = None
    delta: float | None = None


class TrainingVersionComparisonResponse(BaseModel):
    available: bool = False
    status: str
    current_job_id: int
    previous_job_id: int | None = None
    previous_created_at: datetime | None = None
    metrics: list[TrainingVersionComparisonMetric] = Field(default_factory=list)
    summary: str
    comparison_basis: str = "authoritative_val_metrics"
    same_dataset: bool | None = None


class TrainingIterationParentResponse(BaseModel):
    """训练创建前实际会继承的模型版本，而非仅供展示的作业编号。"""

    job_id: int
    task_id: int
    task_name: str | None = None
    model_version_id: int | None = None
    version_no: int | None = None
    # User-facing sequence within the resolved model branch. Keep nullable
    # for historical model rows created before branch numbering existed.
    branch_version_no: int | None = None
    display_name: str
    # Identify the logical dataset that produced the parent snapshot.  The
    # version number alone is ambiguous when a user owns many datasets.
    dataset_id: int | None = None
    dataset_name: str | None = None
    dataset_version_no: int | None = None
    dataset_version_id: int | None = None
    dataset_content_signature: str | None = None
    weights_filename: str = "best.pt"
    created_at: datetime | None = None
    job_status: str | None = None
    job_type: str | None = None
    category: str | None = None
    training_recipe_key: str | None = None
    training_recipe_version: int | None = None
    label_set_signature: str | None = None
    class_order_signature: str | None = None
    source_task_ids: list[int] = Field(default_factory=list)
    model_key: str | None = None
    model_status: str | None = None
    series_id: int | None = None
    series_name: str | None = None
    model_family: str | None = None
    variant_id: int | None = None
    variant_name: str | None = None
    branch_key: str | None = None
    selection_contract_sha256: str | None = None
    selection_contract: dict[str, Any] | None = None


class TrainingIterationPreviewResponse(BaseModel):
    weights_source: Literal["preset", "trained"]
    weights_ref_id: int | None = None
    model_weights: str
    parent: TrainingIterationParentResponse | None = None
    # Machine-readable provenance for the UI and audit log.  Defaults keep
    # older callers valid while making the actual selection rule explicit.
    selection_policy: str = "preset"
    selection_scope: str = "none"
    candidate_count: int = Field(default=0, ge=0)
    fallback_reason: str | None = None
    # Echo the validated global-tag filter used for this preview.  Keeping it
    # in the response lets the wizard prove that the parent lookup and the
    # eventual immutable training contract refer to the same sample subset.
    sample_conditions: dict[str, list[str]] = Field(default_factory=dict)
    ai_report_id: int | None = None
    ai_provider_key: str | None = None
    suggested_params: dict[str, Any] = Field(default_factory=dict)


class TrainingJobResponse(BaseModel):
    id: int
    task_id: int
    algorithm_key: str
    adapter_key: str
    dataset_version_id: int | None = None
    series_id: int | None = None
    variant_id: int | None = None
    series_name: str | None = None
    variant_name: str | None = None
    model_family: str | None = None
    branch_key: str | None = None
    selection_contract_sha256: str | None = None
    selection_contract: dict[str, Any] | None = None
    sample_conditions: dict[str, list[str]] = Field(default_factory=dict)
    parent_model_version_id: int | None = None
    parent_training_job_id: int | None = None
    inheritance_mode: str | None = None
    status: str
    execution_mode: str
    container_image: str | None = None
    epochs: int | None = None
    current_epoch: int | None = None
    progress_percent: int | None = None
    imgsz: int | None = None
    train_sample_count: int | None = None
    val_sample_count: int | None = None
    model_weights: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    task_name: str | None = None
    name: str | None = None
    train_node_id: str | None = None
    runtime_status: str | None = None
    failure_reason: str | None = None
    metrics: dict[str, Any] | None = None
    previous_version_comparison: TrainingVersionComparisonResponse | None = None
    # Quality fields are populated on the detail response.  The list response
    # intentionally leaves them empty so polling the training table stays
    # lightweight; the detail page still uses the canonical YOLO quality
    # assessment instead of inferring a status from raw metrics in the UI.
    training_assessment: dict[str, Any] | None = None
    edge_validation_assessment: dict[str, Any] | None = None
    release_gate_assessment: dict[str, Any] | None = None
    quality_assessment: dict[str, Any] | None = None
    bm1688_export_id: int | None = None
    bm1688_export_status: str | None = None
    bm1688_compile_status: str | None = None
    bm1688_compile_status_label: str | None = None
    bm1688_compile_updated_at: datetime | None = None
    bm1688_bmodel_rel_path: str | None = None
    bm1688_compile_error: str | None = None
    dataset_manifest: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
