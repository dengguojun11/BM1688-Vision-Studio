from __future__ import annotations

from pydantic import BaseModel, Field


class ModelAccessBaseCommand(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider_key: str = Field(..., min_length=1, max_length=64)
    capability: str = Field(default="vision_reasoning", max_length=32)
    endpoint: str = Field(..., min_length=1, max_length=1000)
    model: str = Field(default="", max_length=300)
    enabled: bool = True
    is_default: bool = False
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    verify_tls: bool = False
    max_new_tokens: int = Field(default=1024, ge=1, le=32768)
    auth_type: str = Field(default="none", max_length=32)
    auth_header: str = Field(default="Authorization", max_length=128)
    request_protocol: str = Field(default="auto", max_length=32)
    api_key: str | None = Field(default=None, max_length=4000)


class CreateModelAccessCommand(ModelAccessBaseCommand):
    pass


class UpdateModelAccessCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_key: str | None = Field(default=None, min_length=1, max_length=64)
    capability: str | None = Field(default=None, max_length=32)
    endpoint: str | None = Field(default=None, min_length=1, max_length=1000)
    model: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None
    is_default: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    verify_tls: bool | None = None
    max_new_tokens: int | None = Field(default=None, ge=1, le=32768)
    auth_type: str | None = Field(default=None, max_length=32)
    auth_header: str | None = Field(default=None, max_length=128)
    request_protocol: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, max_length=4000)
