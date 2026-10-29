from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StrategyEvolutionThresholdContractResponse(BaseModel):
    schema_version: str = "v1"
    value_scale: str = "ratio_0_1"
    percent_values_allowed: bool = False
    ranges: dict[str, dict[str, dict[str, float]]] = Field(default_factory=dict)
    operating_ranges: dict[str, dict[str, float]] = Field(default_factory=dict)


class StrategyEvolutionTaskAcceptedResponse(BaseModel):
    status: str = "accepted"
    message: str = ""


class StrategyEvolutionTaskListRowResponse(BaseModel):
    external_task_id: str
    task_name: str
    task_type: str
    source_package_id: str | None = None
    source_type: str = "third_party"
    source_system: str = "strategy_evolution"
    source_external_task_id: str | None = None
    source_external_package_id: str | None = None
    created_by_username: str | None = None
    target: dict[str, Any] = Field(default_factory=dict)
    data_window: dict[str, Any] = Field(default_factory=dict)
    platform_status: str = "accepted"
    automation_stage: str | None = None
    automation_status: str | None = None
    outbound_callback_status: str | None = None
    outbound_callback_attempts: int = 0
    outbound_callback_next_retry_at: datetime | None = None
    automation_attempts: int = 0
    edge_experiment_status: str | None = None
    selected_sample_count: int | None = None
    edge_job_count: int = 0
    latest_edge_job_id: int | None = None
    latest_edge_job_status: str | None = None
    latest_edge_job_start_status: str | None = None
    model_job_count: int = 0
    latest_model_job_id: int | None = None
    latest_model_job_status: str | None = None
    last_message: str = ""
    callback_event_id: str = ""
    finished_at: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyEvolutionTaskDetailResponse(BaseModel):
    external_task_id: str
    task_name: str
    task_type: str
    source_package_id: str | None = None
    source_type: str = "third_party"
    source_system: str = "strategy_evolution"
    source_external_task_id: str | None = None
    source_external_package_id: str | None = None
    created_by_username: str | None = None
    target: dict[str, Any] = Field(default_factory=dict)
    data_window: dict[str, Any] = Field(default_factory=dict)
    experiment: dict[str, Any] = Field(default_factory=dict)
    platform_status: str = "accepted"
    automation_stage: str | None = None
    automation_status: str | None = None
    outbound_callback_status: str | None = None
    outbound_callback_attempts: int = 0
    outbound_callback_next_retry_at: datetime | None = None
    automation_attempts: int = 0
    automation_lease_expires_at: datetime | None = None
    source_train_job_id: int | None = None
    source_export_id: int | None = None
    derived_train_job_id: int | None = None
    derived_export_id: int | None = None
    derived_edge_validation_job_id: int | None = None
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    callback_event_id: str = ""
    finished_at: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyEvolutionPlatformCallbackResponse(BaseModel):
    callback_id: str
    task_id: str
    task_type: str
    status: str
    external_platform: str = "experiment_platform"
