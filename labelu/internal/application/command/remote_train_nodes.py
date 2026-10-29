from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RemoteTrainNodeItemCommand(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, description="节点唯一 id，如 gpu-21")
    name: str = Field(default="", max_length=128)
    enabled: bool = True
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(default="root", max_length=128)
    password: Optional[str] = Field(
        default=None,
        max_length=256,
        description="留空表示不修改已保存密码",
    )
    clear_password: Optional[bool] = Field(
        default=None,
        description="为 true 时清除已保存密码",
    )
    private_key_path: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="平台服务器上的 SSH 私钥路径（免密登录）；留空表示不修改",
    )
    clear_private_key_path: Optional[bool] = Field(
        default=None,
        description="为 true 时清除已保存私钥路径",
    )
    remote_base: str = Field(..., min_length=1, max_length=1024)
    python_path: str = Field(..., min_length=1, max_length=1024)
    agent_path: str = Field(default="", max_length=1024)
    weights_cache_dir: str = Field(default="", max_length=1024)
    cuda_devices: str = Field(default="0", max_length=64)
    payload_download_host: str = Field(
        default="",
        max_length=512,
        description="节点拉取 payload 的 URL 根（内网更快，如 http://10.10.11.25:10000）；留空则自动推断",
    )
    max_concurrent_jobs: int = Field(default=1, ge=1, le=8)


class PutRemoteTrainNodesCommand(BaseModel):
    nodes: List[RemoteTrainNodeItemCommand] = Field(default_factory=list)


class PatchRemoteTrainNodeCommand(BaseModel):
    """单节点局部更新（与列表 PUT 二选一）。"""

    name: Optional[str] = Field(default=None, max_length=128)
    enabled: Optional[bool] = None
    host: Optional[str] = Field(default=None, max_length=256)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    user: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, max_length=256)
    clear_password: Optional[bool] = None
    private_key_path: Optional[str] = Field(default=None, max_length=1024)
    clear_private_key_path: Optional[bool] = None
    remote_base: Optional[str] = Field(default=None, max_length=1024)
    python_path: Optional[str] = Field(default=None, max_length=1024)
    agent_path: Optional[str] = Field(default=None, max_length=1024)
    weights_cache_dir: Optional[str] = Field(default=None, max_length=1024)
    cuda_devices: Optional[str] = Field(default=None, max_length=64)
    payload_download_host: Optional[str] = Field(default=None, max_length=512)
    max_concurrent_jobs: Optional[int] = Field(default=None, ge=1, le=8)
