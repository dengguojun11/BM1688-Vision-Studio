from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModelAccessConfigResponse(BaseModel):
    id: str
    name: str
    provider_key: str
    capability: str
    endpoint: str
    model: str = ""
    enabled: bool = True
    is_default: bool = False
    available: bool = False
    timeout_seconds: int = 300
    verify_tls: bool = False
    max_new_tokens: int = 1024
    auth_type: str = "none"
    auth_header: str = "Authorization"
    request_protocol: str = "auto"
    api_key_configured: bool = False
    api_key_masked: str = ""
    last_check_status: str = "NOT_CHECKED"
    last_check_message: str = ""
    last_check_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ModelAccessConfigTestResponse(BaseModel):
    config: ModelAccessConfigResponse
    ok: bool = False
    endpoint_reachable: bool = False
    response_status: int | None = None
    latency_ms: int | None = None
    message: str = ""


class ModelAccessConfigBundleResponse(BaseModel):
    configs: list[ModelAccessConfigResponse] = Field(default_factory=list)
    config_backend_label: str = "MySQL 配置治理表（system_config_*）"
    supported_provider_keys: list[str] = Field(default_factory=list)
    supported_capabilities: list[str] = Field(default_factory=list)
    supported_request_protocols: list[str] = Field(default_factory=list)
