from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderRegistryResponse(BaseModel):
    id: int
    provider_key: str
    provider_version: str
    display_name: str
    provider_kind: str
    capabilities: list[str] = Field(default_factory=list)
    input_contract_version: str
    output_contract_version: str
    runtime_adapter: str
    supported_media_types: list[str] = Field(default_factory=list)
    supported_chips: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    status: str
    runtime_registered: bool = False
    runtime_healthy: bool = False
    runtime_ready: bool = False
    runtime_last_error: str | None = None
    hil_verified: bool = False
    hil_evidence: dict[str, Any] | None = None
    runtime_evidence: dict[str, Any] | None = None
    production_ready: bool = False
    # Discovery deliberately reports evidence separately from status.
    available: bool = False
    image_digest: str | None = None
    toolchain_version: str | None = None
    availability_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
