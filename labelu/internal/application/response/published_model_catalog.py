from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PublishedModelAliasResponse(BaseModel):
    id: int
    source_system: str
    external_model_id: str
    alias_type: str
    created_at: Optional[datetime] = None


class PublishedModelClassLabelResponse(BaseModel):
    class_index: int = Field(description="模型包内从 0 开始的类别序号")
    label_key: Optional[str] = Field(default=None, description="中文标签名称")
    label_value: str = Field(description="模型推理使用的英文类别标识")
    color: Optional[str] = None
    status: Optional[str] = None
    annotation_kind: Optional[str] = None
    matched: bool = Field(description="是否已匹配 LabelU 系统标签")


class PublishedModelResponse(BaseModel):
    catalog_id: int
    model_id: str = Field(description="兼容旧系统的稳定对外模型 ID")
    download_path: str = Field(description="稳定的 LabelU 对外下载路径")
    aicm_model_set_id: Optional[int] = None
    aicm_environment_id: Optional[str] = None
    export_id: Optional[int] = None
    yolo_train_job_id: Optional[int] = None
    task_id: Optional[int] = None
    name: str = ""
    display_name: str = ""
    source_type: str
    source_system: str
    status: str
    is_public: bool
    download_enabled: bool
    availability_status: str
    chip_type: Optional[str] = None
    algorithm_type: Optional[str] = None
    precision: Optional[str] = Field(
        default=None,
        description="模型实际发布使用的 BModel 编译精度：int8 | fp16 | fp32；历史模型无法确认时为空",
    )
    resolution: Optional[str] = None
    map50: Optional[float] = None
    class_names: List[str] = Field(default_factory=list)
    class_labels: List[PublishedModelClassLabelResponse] = Field(default_factory=list)
    remote_download_url: Optional[str] = None
    local_package_available: bool = False
    package_size_bytes: Optional[int] = None
    package_sha256: Optional[str] = None
    package_md5: Optional[str] = None
    publisher_username: Optional[str] = None
    publish_job_id: Optional[str] = None
    published_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    label_group_version: Optional[str] = Field(
        default=None, description="相同标签集合按时间排列的序号，不代表效果升级"
    )
    label_group_size: int = 0
    related_model_count: int = Field(
        default=0, description="至少共享一个标签的其他模型数量"
    )
    aliases: List[PublishedModelAliasResponse] = Field(default_factory=list)


class PublishedModelSummaryResponse(BaseModel):
    total: int = 0
    active: int = 0
    public: int = 0
    unavailable: int = 0
    legacy: int = 0


class PublishedModelListResponse(BaseModel):
    summary: PublishedModelSummaryResponse
    models: List[PublishedModelResponse] = Field(default_factory=list)


class PublishedModelSyncResponse(BaseModel):
    history_scanned: int = 0
    remote_scanned: int = 0
    created: int = 0
    updated: int = 0
    unavailable: int = 0
    environment_id: str = ""


class PublishedModelDownloadResponse(BaseModel):
    catalog_id: int
    model_id: str
    aicm_model_set_id: Optional[int] = None
    download_url: str
    filename: str
