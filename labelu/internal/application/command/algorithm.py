from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AlgorithmKind = Literal["model", "pipeline", "rule_template"]


class CreateAlgorithmDefinitionCommand(BaseModel):
    algorithm_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    task_type: str = Field(min_length=1, max_length=64)
    algorithm_group_id: int = Field(gt=0)
    label_set_signature: str | None = Field(default=None, max_length=64)
    required_tools: list[str] = Field(default_factory=list)
    required_attributes: list[str] = Field(default_factory=list)
    training_adapter: str = Field(min_length=1, max_length=128)
    evaluation_adapter: str = Field(min_length=1, max_length=128)
    supported_chips: list[str] = Field(default_factory=list)
    default_recipe: str | None = Field(default=None, max_length=128)
    input_contract: dict[str, Any] | None = None
    output_contract: dict[str, Any] | None = None
    algorithm_kind: AlgorithmKind = "model"
    runtime_adapter: str | None = Field(default=None, max_length=128)
    rule_schema: dict[str, Any] | None = None
    supported_formats: list[str] = Field(default_factory=list)
    min_samples: dict[str, Any] | None = None
    quality_gate: dict[str, Any] | None = None
    risk_level: str | None = Field(default="standard", max_length=24)
    annotation_contract_version: str | None = Field(default="v1", max_length=64)
    catalog_kind: str = Field(default="internal", min_length=1, max_length=32)
    status: str = Field(default="ACTIVE", min_length=1, max_length=24)


class UpdateAlgorithmDefinitionCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    task_type: str | None = Field(default=None, min_length=1, max_length=64)
    algorithm_group_id: int | None = Field(default=None, gt=0)
    label_set_signature: str | None = Field(default=None, max_length=64)
    required_tools: list[str] | None = None
    required_attributes: list[str] | None = None
    training_adapter: str | None = Field(default=None, min_length=1, max_length=128)
    evaluation_adapter: str | None = Field(default=None, min_length=1, max_length=128)
    supported_chips: list[str] | None = None
    default_recipe: str | None = Field(default=None, max_length=128)
    input_contract: dict[str, Any] | None = None
    output_contract: dict[str, Any] | None = None
    algorithm_kind: AlgorithmKind | None = None
    runtime_adapter: str | None = Field(default=None, max_length=128)
    rule_schema: dict[str, Any] | None = None
    supported_formats: list[str] | None = None
    min_samples: dict[str, Any] | None = None
    quality_gate: dict[str, Any] | None = None
    risk_level: str | None = Field(default=None, max_length=24)
    annotation_contract_version: str | None = Field(default=None, max_length=64)
    catalog_kind: str | None = Field(default=None, min_length=1, max_length=32)
    status: str | None = Field(default=None, min_length=1, max_length=24)
