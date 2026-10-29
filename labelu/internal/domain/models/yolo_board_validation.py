"""BM1688 算能盒板端验证任务（由内网 Agent 主动拉取公网 LabelU）。"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class YoloBoardValidation(Base):
    __tablename__ = "yolo_board_validation"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    export_id = Column(
        Integer,
        ForeignKey("yolo_bm1688_export.id"),
        nullable=False,
        index=True,
    )
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    yolo_train_job_id = Column(Integer, ForeignKey("yolo_train_job.id"), nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    report_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
