from pydantic import BaseModel, Field


class EdgeSshConfigResponse(BaseModel):
    enabled: bool = False
    device_id: str = ""
    device_name: str = ""
    host: str = ""
    port: int = 22
    user: str = "linaro"
    password_configured: bool = Field(default=False)
    password_source_type: str = Field(default="")
    password_masked_hint: str = Field(default="")
    password_updated_at: str | None = Field(default=None)
    password_runtime_available: bool = Field(default=False)
    private_key_path_source_type: str = Field(default="")
    private_key_path_masked: str = Field(default="")
    private_key_path_configured: bool = Field(default=False)
    private_key_path_runtime_available: bool = Field(default=False)
    private_key_path_updated_at: str | None = Field(default=None)
    edge_root: str = "/data/labelu_edge"
    remote_base: str = "/data/labelu_edge/jobs"
    sophon_demo_root: str = ""
    auto_after_export: bool = False
    run_coco_eval: bool = False
    docker_container: str = Field(default="bm1688")
    docker_use_sudo: bool = Field(default=True)
    probe_status: str = Field(default="unknown")
    probe_stage: str = Field(default="")
    probe_code: str = Field(default="")
    probe_message: str = Field(default="")
    probe_checked_at: str | None = Field(default=None)
    last_validation_status: str = Field(default="")
    last_validation_at: str | None = Field(default=None)
    last_used_at: str | None = Field(default=None)
    auto_run_ready: bool = Field(default=False)
    doc_hint: str = ""


class EdgeSshProbeResponse(BaseModel):
    status: str = Field(default="unknown")
    stage: str = Field(default="")
    code: str = Field(default="")
    message: str = Field(default="")
    resolved_host: str = Field(default="")
    resolved_user: str = Field(default="")
    remote_base: str = Field(default="")
    password_source: str = Field(default="")
    private_key_path_source: str = Field(default="")
    checked_at: str | None = Field(default=None)
