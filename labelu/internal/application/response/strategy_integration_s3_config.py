from typing import List, Optional

from pydantic import BaseModel, Field


class StrategyIntegrationS3ConnectionResponse(BaseModel):
    id: str
    name: str
    endpoint: str
    remark: str = ""
    is_active: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyIntegrationS3ConnectionsBundleResponse(BaseModel):
    active_connection_id: str = ""
    connections: List[StrategyIntegrationS3ConnectionResponse] = Field(default_factory=list)
    config_backend_label: str = "MySQL 配置治理表（system_config_*）"


class StrategyIntegrationS3ConnectionTestResponse(BaseModel):
    """管理员发起的只读 S3 连接探测结果。"""

    connection_id: str
    endpoint: str
    bucket: str = ""
    ok: bool = False
    endpoint_reachable: bool = False
    head_bucket_ok: bool = False
    list_objects_ok: bool = False
    object_count: int = 0
    checked_at: Optional[str] = None
    error_stage: Optional[str] = None
    message: str
