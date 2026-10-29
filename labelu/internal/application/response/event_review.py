"""事件审核模块 - 响应模型"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EventReviewItem(BaseModel):
    """告警事件列表项"""
    id: int
    alarm_id: Optional[str] = None
    camera_name: Optional[str] = None
    event_type: Optional[str] = None
    event_time: Optional[str] = None
    status: str = "pending"  # pending / reviewing / skipped / transferred / cleared
    reviewer: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: Optional[str] = None


class EventReviewImage(BaseModel):
    """事件关联图片"""
    id: int
    url: str
    filename: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class EventReviewDetail(BaseModel):
    """告警事件详情"""
    id: int
    alarm_id: Optional[str] = None
    camera_name: Optional[str] = None
    camera_location: Optional[str] = None
    event_type: Optional[str] = None
    event_time: Optional[str] = None
    alarm_info: Optional[Dict[str, Any]] = None
    status: str = "pending"
    reviewer: Optional[str] = None
    images: List[EventReviewImage] = Field(default_factory=list)
    created_at: Optional[str] = None


class EventReviewProgress(BaseModel):
    """审核进度"""
    total_pending: int = 0
    reviewing_count: int = 0
    done_today: int = 0
    current_batch: int = 0
    batch_total: int = 0
