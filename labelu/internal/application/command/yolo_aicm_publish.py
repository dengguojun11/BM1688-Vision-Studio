from typing import Literal, Optional

from pydantic import BaseModel, Field


class PatchAicmPublishConfigCommand(BaseModel):
    base_url: Optional[str] = Field(None, description="AICM 后台根 URL，如 http://192.168.54.169:8102")
    username: Optional[str] = None
    password_secret_action: Literal["keep", "set", "clear"] = Field(
        default="keep",
        description="敏感字段三态：keep 保留、set 加密保存新密码、clear 清空密码",
    )
    password: Optional[str] = Field(
        None,
        min_length=1,
        max_length=256,
        description="AICM 登录密码，仅在 set 时提交；服务端加密后写入 MySQL",
    )
    chip_type: Optional[str] = Field(None, description="上传 chip_type，默认 SOPHGO,BM1688,NPU")
    algorithm_type: Optional[str] = Field(None, description="上传 algorithm_type，默认 Detection")
    verify_ssl: Optional[bool] = Field(None, description="HTTPS 是否校验证书")


class CreateAicmPublishEnvironmentCommand(BaseModel):
    name: Optional[str] = Field(
        None,
        max_length=64,
        description="环境名称；省略时根据 IP 和端口自动生成",
    )
    base_url: str = Field(..., description="AICM 后台根 URL")
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="AICM 登录密码；服务端加密后写入 MySQL",
    )
    chip_type: Optional[str] = Field(None, description="上传 chip_type")
    algorithm_type: Optional[str] = Field(None, description="上传 algorithm_type")
    verify_ssl: bool = Field(default=False, description="HTTPS 是否校验证书")
    set_active: bool = Field(default=True, description="创建后设为当前发布环境")


class UpdateAicmPublishEnvironmentCommand(BaseModel):
    name: Optional[str] = Field(None, max_length=64)
    base_url: Optional[str] = None
    username: Optional[str] = Field(None, max_length=128)
    password_secret_action: Literal["keep", "set", "clear"] = Field(
        default="keep",
        description="敏感字段三态：keep 保留、set 加密保存新密码、clear 清空密码",
    )
    password: Optional[str] = Field(
        None,
        min_length=1,
        max_length=256,
        description="新密码，仅在 set 时提交；服务端加密后写入 MySQL",
    )
    chip_type: Optional[str] = None
    algorithm_type: Optional[str] = None
    verify_ssl: Optional[bool] = None


class SetActiveAicmPublishEnvironmentCommand(BaseModel):
    environment_id: str = Field(..., min_length=1, description="要设为当前发布的环境 id")


class AicmPublishRequestCommand(BaseModel):
    export_id: int = Field(..., ge=1, description="BM1688 导出记录 id")
    environment_id: Optional[str] = Field(
        None,
        description="发布环境 id；省略则使用当前激活环境",
    )
    keep_template_cache: bool = Field(
        default=False,
        description="为 true 时复用本地已缓存的 AICM 模板 zip；默认 false 每次按搜索命中重新下载最新模板",
    )
    bmodel_precision: Optional[Literal["int8", "fp16", "fp32"]] = Field(
        default=None,
        description="选用哪种精度编译产物：int8 | fp16 | fp32；省略默认 int8。"
        "打入 AICM zip 时使用编译产物文件名（如 yolov11n_fp16_1b.bmodel），"
        "并同步更新 video.json ModelPath；若与模板占位名不同则移除模板旧 bmodel。",
    )
    bmodel_rel_path: Optional[str] = Field(
        default=None,
        description="（兼容旧客户端）导出目录 models/ 下相对路径；若与 bmodel_precision 同时提供则以 precision 为准",
    )
