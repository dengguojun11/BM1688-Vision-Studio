from typing import List

from pydantic import BaseModel, Field


class EdgeDeviceResponse(BaseModel):
    id: str
    name: str = ""
    is_active: bool = False
    enabled: bool = True
    # Connection credentials and remote filesystem locations are only
    # populated for system administrators.  ``None`` lets the HTTP route use
    # ``response_model_exclude_none`` for a real field-level projection,
    # rather than returning misleading placeholder values to regular users.
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password_source_type: str | None = Field(default=None)
    password_masked_hint: str | None = Field(default=None)
    password_configured: bool | None = Field(default=None)
    password_runtime_available: bool | None = Field(default=None)
    password_updated_at: str | None = None
    private_key_path_source_type: str | None = Field(default=None)
    private_key_path_masked: str | None = Field(default=None)
    private_key_path_configured: bool | None = Field(default=None)
    private_key_path_runtime_available: bool | None = Field(default=None)
    private_key_path_updated_at: str | None = None
    edge_root: str | None = None
    remote_base: str | None = None
    sophon_demo_root: str | None = None
    auto_after_export: bool = False
    run_coco_eval: bool = False
    connect_timeout_sec: int = 30
    validation_timeout_sec: int = 7200
    docker_container: str = Field(default="bm1688")
    docker_use_sudo: bool = True
    validation_channel: str = "ssh"
    board_agent_status: str = Field(default="unknown")
    board_agent_last_heartbeat_at: str | None = None
    board_agent_environment_ready: bool = False
    board_agent_capabilities: dict | None = None
    board_agent_message: str | None = None
    agent_bootstrap_status: str = Field(default="NOT_STARTED")
    # Non-secret capability flag used by the admin UI to distinguish a
    # deliberately disabled control plane from an Agent that still needs
    # onboarding.
    agent_bootstrap_control_plane_enabled: bool = False
    # Non-secret diagnostics for the page-driven control-plane setup.  The
    # actual Token and callback URL are never returned.
    agent_control_plane_source: str = Field(default="default")
    agent_control_plane_message: str | None = None
    agent_control_plane_public_base_url_configured: bool = False
    agent_bootstrap_stage: str = Field(default="")
    agent_bootstrap_code: str | None = None
    agent_bootstrap_message: str | None = None
    agent_bootstrap_version: str | None = None
    agent_bootstrap_updated_at: str | None = None
    probe_status: str = Field(default="unknown")
    probe_stage: str = Field(default="")
    probe_code: str | None = None
    probe_message: str | None = None
    probe_checked_at: str | None = None
    last_probe_status: str = Field(default="unknown")
    last_probe_stage: str = Field(default="")
    last_probe_code: str | None = None
    last_probe_message: str | None = None
    last_probe_checked_at: str | None = None
    last_validation_status: str = Field(default="")
    last_validation_at: str | None = None
    environment_status: str = Field(default="unknown")
    environment_stage: str = Field(default="")
    environment_code: str | None = None
    environment_message: str | None = None
    environment_checked_at: str | None = None
    # The raw report can contain remote paths, command output, and hardware
    # inventory.  It is only exposed in the administrator projection.
    environment_report: dict | None = None
    last_used_at: str | None = None
    scheduling_status: str = Field(default="idle")
    is_busy: bool = Field(default=False)
    active_job_id: int | None = None
    available_for_dispatch: bool = Field(default=False)
    created_at: str | None = None
    updated_at: str | None = None


class EdgeDeviceListResponse(BaseModel):
    active_device_id: str = Field(default="")
    devices: List[EdgeDeviceResponse] = Field(default_factory=list)
    # Pagination fields are kept inside the existing ``data`` envelope so
    # clients that only read ``active_device_id``/``devices`` remain
    # compatible with the registry endpoint.  ``page`` is zero-based and is
    # omitted for legacy, unpaged requests.
    total: int = 0
    page: int | None = None
    size: int | None = None
    config_backend_label: str = Field(
        default="MySQL（system_config_value）",
        description="当前配置主读写来源",
    )
    doc_hint: str = ""
