from typing import Literal, Optional

from pydantic import BaseModel, Field, root_validator


class CreateEdgeDeviceCommand(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    enabled: bool = True
    host: Optional[str] = Field(default=None, max_length=256)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(default="linaro", min_length=1, max_length=128)
    password: Optional[str] = Field(default=None, max_length=256, description="SSH 密码，仅在请求中传输并加密存入 MySQL")
    private_key_path: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="LabelU 服务端可读取的 SSH 私钥路径；私钥内容不会上传或回传",
    )
    edge_root: Optional[str] = Field(default=None, max_length=1024)
    remote_base: Optional[str] = Field(default=None, max_length=1024)
    sophon_demo_root: Optional[str] = Field(default=None, max_length=1024)
    auto_after_export: bool = True
    run_coco_eval: bool = False
    connect_timeout_sec: int = Field(default=30, ge=1, le=3600)
    validation_timeout_sec: int = Field(default=7200, ge=1, le=86400)
    docker_container: str = Field(default="", max_length=128)
    docker_use_sudo: bool = True
    validation_channel: Literal["ssh", "board_agent"] = "ssh"
    set_active: bool = True
    # The web device-management form opts into DB-backed Agent onboarding.
    # Keep the default false for older API clients that intentionally retain
    # the legacy SSH-only behavior.
    auto_configure_agent: bool = False

    @root_validator(skip_on_failure=True)
    def validate_channel_credentials(cls, values):
        channel = str(values.get("validation_channel") or "ssh").strip().lower()
        if channel == "ssh":
            if not str(values.get("host") or "").strip():
                raise ValueError("SSH 通道必须填写主机地址")
            if not (
                str(values.get("password") or "").strip()
                or str(values.get("private_key_path") or "").strip()
            ):
                raise ValueError("SSH 通道必须填写 SSH 密码或私钥路径")
        return values


class PatchEdgeDeviceCommand(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    enabled: Optional[bool] = None
    host: Optional[str] = Field(default=None, max_length=256)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    user: Optional[str] = Field(default=None, max_length=128)
    password_secret_action: Literal["keep", "set", "clear"] = Field(default="keep")
    password: Optional[str] = Field(default=None, max_length=256, description="新 SSH 密码；留空并使用 keep 时保留原密码")
    private_key_path_secret_action: Literal["keep", "set", "clear"] = Field(default="keep")
    private_key_path: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="LabelU 服务端可读取的 SSH 私钥路径；留空并使用 keep 时保留原路径",
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
    connect_timeout_sec: Optional[int] = Field(default=None, ge=1, le=3600)
    validation_timeout_sec: Optional[int] = Field(default=None, ge=1, le=86400)
    docker_container: Optional[str] = Field(default=None, max_length=128)
    docker_use_sudo: Optional[bool] = None
    validation_channel: Optional[Literal["ssh", "board_agent"]] = None
    auto_configure_agent: bool = False

    @root_validator(pre=False, skip_on_failure=True)
    def normalize_private_key_action(cls, values):
        if values.get("clear_private_key_path") is True:
            values["private_key_path_secret_action"] = "clear"
        elif (
            values.get("private_key_path_secret_action") == "set"
            and not str(values.get("private_key_path") or "").strip()
        ):
            raise ValueError("private_key_path_secret_action=set 时必须提供 private_key_path")
        return values


class SetActiveEdgeDeviceCommand(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
