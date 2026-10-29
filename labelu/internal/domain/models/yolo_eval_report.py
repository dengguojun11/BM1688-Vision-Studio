from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from labelu.internal.common.db import Base


class YoloEvalReport(Base):
    __tablename__ = "yolo_eval_report"
    __table_args__ = (
        # operation_id 允许为空以兼容历史报告；新报告由服务层写入非空代次。
        # MySQL 对 NULL 唯一键的多行语义正好保留历史重复数据。
        UniqueConstraint(
            "yolo_train_job_id",
            "report_kind",
            "provider_key",
            "operation_id",
            name="uq_yolo_eval_report_operation",
        ),
        Index(
            "ix_yolo_eval_report_job_kind_operation",
            "yolo_train_job_id",
            "report_kind",
            "operation_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    yolo_train_job_id = Column(Integer, ForeignKey("yolo_train_job.id"), index=True)
    provider_key = Column(String(32), nullable=False)
    report_kind = Column(String(32), nullable=False)
    operation_id = Column(String(64), nullable=True, index=True)
    body = Column(Text, nullable=True)
    suggested_params = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
