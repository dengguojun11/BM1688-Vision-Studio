from typing import Literal, Optional

from pydantic import BaseModel, Field


class UpdatePublishedModelCommand(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=256)
    status: Optional[Literal["active", "hidden", "revoked"]] = None
    is_public: Optional[bool] = None
    download_enabled: Optional[bool] = None
    expected_download_enabled: Optional[bool] = Field(
        default=None,
        description="修改下载开关时客户端看到的原值，用于并发冲突检测",
    )


class CreatePublishedModelAliasCommand(BaseModel):
    source_system: str = Field(min_length=1, max_length=64)
    external_model_id: str = Field(min_length=1, max_length=128)
    alias_type: Literal["third_party", "legacy", "aicm"] = "third_party"


class SyncPublishedModelsCommand(BaseModel):
    environment_id: Optional[str] = Field(default=None, max_length=64)
    include_remote_models: bool = True
