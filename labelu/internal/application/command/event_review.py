"""事件审核模块 - 命令/请求模型"""

from typing import Optional

from pydantic import BaseModel, Field


class EventReviewListCommand(BaseModel):
    """告警事件列表查询参数"""
    page: int = Field(0, ge=0, description="页码（0 起始）")
    size: int = Field(10, ge=1, le=100, description="每页条数")
    status: Optional[str] = Field(None, description="筛选状态：pending / reviewing / done")
    keyword: Optional[str] = Field(None, description="搜索关键词")


class EventReviewClaimCommand(BaseModel):
    """认领一批待审核事件"""
    count: int = Field(10, ge=1, le=20, description="认领数量")


class EventReviewActionCommand(BaseModel):
    """对单个事件执行审核动作"""
    event_id: int = Field(..., description="事件 ID")
    action: str = Field(..., description="动作：skip / transfer / clear")
    event_type: Optional[str] = Field(None, description="事件类型（转工单时必填）")
    remark: Optional[str] = Field(None, description="备注")


class EventReviewSetTokenCommand(BaseModel):
    """前端设置老平台 API token"""
    token: str = Field(..., description="后海平台 ACCESS_TOKEN")
