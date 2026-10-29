from typing import List, Optional

from pydantic import BaseModel, Field


class RemoteTrainNodeResponse(BaseModel):
    id: str
    name: str = ""
    enabled: bool = True
    host: str = ""
    port: int = 22
    user: str = "root"
    password_configured: bool = Field(
        default=False,
        description="是否已保存密码（不回传明文）",
    )
    private_key_path_configured: bool = Field(
        default=False,
        description="是否已配置平台侧 SSH 私钥路径（不回传路径明文时可仅看此标志）",
    )
    remote_base: str = ""
    python_path: str = ""
    agent_path: str = ""
    weights_cache_dir: str = ""
    cuda_devices: str = "0"
    payload_download_host: str = Field(
        default="",
        description="节点 HTTP 拉取 payload 的基址（内网地址可显著提速）",
    )
    max_concurrent_jobs: int = 1
    env_ready: bool = Field(
        default=False,
        description="最近一次测试连接是否通过（路径、Python、GPU）",
    )
    last_test_at: Optional[str] = None
    last_test_message: Optional[str] = None
    doc_hint: str = ""


class RemoteTrainNodesListResponse(BaseModel):
    nodes: List[RemoteTrainNodeResponse] = Field(default_factory=list)
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="当前配置主读写来源",
    )
    legacy_sync_file: str = Field(default="", description="兼容同步文件路径（非主读写来源）")
    doc_hint: str = ""


class RemoteTrainNodeTestResponse(BaseModel):
    node_id: str
    ok: bool
    message: str = ""
    checks: List[str] = Field(default_factory=list)
    env_ready: bool = False
