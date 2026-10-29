from typing import Literal, Optional

from pydantic import BaseModel, Field


class PatchEdgeSshConfigCommand(BaseModel):
    enabled: Optional[bool] = None
    host: Optional[str] = Field(default=None, max_length=256)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    user: Optional[str] = Field(default=None, max_length=128)
    password_secret_action: Literal["keep", "set", "clear"] = Field(
        default="keep",
        description="敏感字段三态：keep 保留、set 更新加密密码、clear 清空密码",
    )
    password: Optional[str] = Field(default=None, max_length=256)
    private_key_path_secret_action: Literal["keep", "set", "clear"] = Field(
        default="keep",
        description="私钥路径三态：keep 保留、set 更新、clear 清空",
    )
    private_key_path: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="LabelU 服务端可读取的 SSH 私钥路径；不上传私钥内容",
    )
    clear_private_key_path: Optional[bool] = Field(
        default=None,
        description="为 true 时清除已保存的 SSH 私钥路径",
    )
    edge_root: Optional[str] = Field(default=None, max_length=1024)
    remote_base: Optional[str] = Field(default=None, max_length=1024)
    sophon_demo_root: Optional[str] = Field(default=None, max_length=1024)
    auto_after_export: Optional[bool] = None
    run_coco_eval: Optional[bool] = None
    docker_container: Optional[str] = Field(
        default=None,
        max_length=128,
        description="边端 Docker 容器名（如 bm1688）；空字符串表示宿主机直接执行",
    )
    docker_use_sudo: Optional[bool] = None
