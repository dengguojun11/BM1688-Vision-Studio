from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EdgeAgentResponse(BaseModel):
    agent_id: str
    name: str
    project_id: int | None = None
    status: str
    agent_version: str | None = None
    platform: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    desired_state: dict[str, Any] = Field(default_factory=dict)
    reported_state: dict[str, Any] = Field(default_factory=dict)
    last_heartbeat_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EdgeAgentRegistrationResponse(BaseModel):
    agent: EdgeAgentResponse
    agent_token: str


class EdgeAgentDesiredStateResponse(BaseModel):
    agent_id: str
    desired_state: dict[str, Any] = Field(default_factory=dict)
    server_time: datetime | None = None


class EdgeAgentReleaseResponse(BaseModel):
    id: int
    release_key: str
    version: str
    project_id: int | None = None
    artifact_sha256: str
    artifact_size: int
    chunk_size: int
    chunk_count: int
    signature: str | None = None
    status: str


class EdgeAgentDeploymentResponse(BaseModel):
    id: int
    release_id: int
    agent_id: str
    state: str
    downloaded_bytes: int
    verified_sha256: str | None = None
    active_version: str | None = None
    previous_version: str | None = None
    failure_reason: str | None = None
    attempts: int
    last_chunk_index: int | None = None
    updated_at: datetime | None = None


class EdgeAgentManifestResponse(BaseModel):
    release: EdgeAgentReleaseResponse
    deployment: EdgeAgentDeploymentResponse
    download_url: str
    verify_url: str
    activate_url: str
    rollback_url: str
