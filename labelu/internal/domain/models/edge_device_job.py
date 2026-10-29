from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class EdgeDeviceJob(Base):
    __tablename__ = "edge_device_job"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    export_job_id = Column(Integer, ForeignKey("export_job.id"), index=True, nullable=False)
    task_id = Column(Integer, ForeignKey("task.id"), index=True, nullable=False)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    status = Column(String(32), index=True, default="PENDING")
    device_id = Column(String(128), index=True, nullable=True)
    payload = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
