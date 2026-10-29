from typing import Any

from pydantic import BaseModel, Field


class RegisterEdgeAgentCommand(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    name: str = Field(min_length=1, max_length=160)
    enrollment_token: str = Field(min_length=1, max_length=512)
    project_id: int | None = Field(default=None, gt=0)
    agent_version: str | None = Field(default=None, max_length=64)
    platform: str | None = Field(default=None, max_length=64)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class HeartbeatEdgeAgentCommand(BaseModel):
    agent_version: str | None = Field(default=None, max_length=64)
    platform: str | None = Field(default=None, max_length=64)
    metrics: dict[str, Any] = Field(default_factory=dict)
    reported_state: dict[str, Any] = Field(default_factory=dict)


class ReportEdgeAgentCapabilitiesCommand(BaseModel):
    capabilities: dict[str, Any] = Field(default_factory=dict)


class CreateEdgeAgentReleaseCommand(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    project_id: int | None = Field(default=None, gt=0)
    artifact_path: str = Field(min_length=1, max_length=1024)
    artifact_sha256: str = Field(min_length=64, max_length=64)
    chunk_size: int = Field(default=1024 * 1024, ge=64 * 1024, le=8 * 1024 * 1024)
    signature: str | None = Field(default=None, max_length=4096)
    agent_ids: list[str] = Field(default_factory=list, max_items=500)


class VerifyEdgeAgentOtaCommand(BaseModel):
    downloaded_sha256: str = Field(min_length=64, max_length=64)
    downloaded_bytes: int = Field(ge=0)


class ActivateEdgeAgentOtaCommand(BaseModel):
    active_version: str = Field(min_length=1, max_length=64)


class RollbackEdgeAgentOtaCommand(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)
