"""Commands for SSH based Board Agent bootstrap."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class BootstrapEdgeAgentCommand(BaseModel):
    """Start or preview installation of the resident Board Agent.

    ``confirm_install`` is deliberately explicit.  A normal SSH probe must not
    silently turn into a persistent systemd installation.  ``dry_run`` can be
    used by an operator UI to render the exact release metadata before the
    confirmation step.
    """

    device_id: str = Field(..., min_length=1, max_length=64)
    agent_version: Optional[str] = Field(default=None, max_length=64)
    confirm_install: bool = False
    dry_run: bool = False
    wait_for_heartbeat: bool = True
    heartbeat_timeout_sec: int = Field(default=120, ge=0, le=3600)
    heartbeat_poll_interval_sec: float = Field(default=2.0, ge=0.2, le=60.0)
    provision_runtime: bool = True
    require_environment_ready: bool = True
    run_coco_eval: Optional[bool] = None
    poll_interval_sec: int = Field(default=30, ge=10, le=3600)
