from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class AnnotationDailyUserRow(BaseModel):
    """某用户在某天真实修改过的图片张数。"""

    user_id: int
    username: str
    stat_date: date
    done_count: int


class AnnotationSampleTimingRow(BaseModel):
    """单张真实修改图片的估算用时（由创建时间到当天最后一次真实修改时间近似）。"""

    sample_id: int
    task_id: int
    task_name: Optional[str] = None
    filename: Optional[str] = None
    user_id: int
    username: str
    duration_seconds: float = Field(description="完成时刻与样本创建时刻之差（秒）")
    annotated_at: datetime
    annotated_count: int = 0


class AnnotationStatsSummary(BaseModel):
    total_done: int = 0
    total_annotators: int = 0
    avg_duration_seconds: Optional[float] = None
    today_done: int = 0
    date_from: date
    date_to: date


class AnnotationStatsDashboardResponse(BaseModel):
    summary: AnnotationStatsSummary
    daily_by_user: List[AnnotationDailyUserRow] = Field(default_factory=list)
    sample_timings: List[AnnotationSampleTimingRow] = Field(default_factory=list)
    sample_timings_total: int = 0
    duration_note: str = Field(
        default="单张用时 = 图片创建时间到当天最后一次真实修改时间（近似值，非页面停留计时）"
    )
