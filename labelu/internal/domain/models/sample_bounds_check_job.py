from enum import Enum
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class SampleBoundsCheckStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SampleBoundsCheckJob(Base):
    __tablename__ = "sample_bounds_check_job"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), index=True)
    created_by = Column(Integer, ForeignKey("user.id"))
    status = Column(String(32), default=SampleBoundsCheckStatus.PENDING.value)
    sample_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    normal_count = Column(Integer, default=0)
    incomplete_count = Column(Integer, default=0)
    abnormal_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
