"""Responses for Board Agent bootstrap and provisioning status."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EdgeAgentBootstrapResponse(BaseModel):
    device_id: str
    status: str = "NOT_STARTED"
    stage: str = ""
    code: str = ""
    message: str = ""
    agent_version: str = ""
    artifact_sha256: str = ""
    service_name: str = "labelu-board-agent.service"
    remote_root: str = "/data/labelu_edge"
    remote_version_root: str = ""
    environment_ready: bool = False
    board_agent_status: str = "unknown"
    board_agent_last_heartbeat_at: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    heartbeat_deadline_at: str | None = None
    dry_run: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
