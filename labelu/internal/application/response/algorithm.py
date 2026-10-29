from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlgorithmGroupResponse(BaseModel):
    id: int
    group_key: str
    name: str
    description: str | None = None
    status: str
    sort_order: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AlgorithmDefinitionResponse(BaseModel):
    id: int
    algorithm_key: str
    name: str
    description: str | None = None
    task_type: str
    algorithm_group_id: int
    group_key: str | None = None
    group_name: str | None = None
    label_set_signature: str | None = None
    required_tools: list[str]
    required_attributes: list[str]
    training_adapter: str
    evaluation_adapter: str
    supported_chips: list[str]
    default_recipe: str | None = None
    input_contract: dict[str, Any] | None = None
    output_contract: dict[str, Any] | None = None
    algorithm_kind: str = "model"
    runtime_adapter: str | None = None
    rule_schema: dict[str, Any] | None = None
    supported_formats: list[str] = Field(default_factory=list)
    min_samples: dict[str, Any] | None = None
    quality_gate: dict[str, Any] | None = None
    risk_level: str | None = None
    annotation_contract_version: str | None = None
    catalog_kind: str = "internal"
    status: str
    adapter_available: bool
    capability_state: str = "REGISTERED"
    trainable: bool = False
    quality_ready: bool = False
    publishable: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AlgorithmCapabilityResponse(BaseModel):
    algorithm_key: str
    adapter_key: str
    execution_mode: str = "in_process"
    task_type: str
    supported_chips: list[str]
    stages: list[str]
    implemented: list[str]
    available: bool
    capability_state: str = "REGISTERED"
    trainable: bool = False
    quality_ready: bool = False
    publishable: bool = False
    protocol_version: str | None = None
    worker: str | None = None
