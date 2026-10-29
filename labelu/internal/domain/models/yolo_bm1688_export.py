"""训练完成的 YOLO 权重导出为 ONNX（及可选 BModel）供算能 BM1688 等 TPU 使用。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects import mysql

from labelu.internal.common.db import Base


class YoloBm1688Export(Base):
    __tablename__ = "yolo_bm1688_export"
    __table_args__ = (
        Index(
            "ix_yolo_bm1688_export_worker_scan",
            "status",
            "worker_action",
            "queue_lease_expires_at",
            "id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    yolo_train_job_id = Column(
        Integer, ForeignKey("yolo_train_job.id"), nullable=False, index=True
    )
    source_type = Column(String(32), nullable=False, default="internal", index=True)
    source_system = Column(String(64), nullable=True, index=True)
    source_external_task_id = Column(String(128), nullable=True, index=True)
    source_external_package_id = Column(String(128), nullable=True, index=True)
    source_integration_id = Column(Integer, nullable=True, index=True)
    status = Column(String(32), nullable=False, default="PENDING")
    imgsz = Column(Integer, nullable=False, default=640)
    onnx_rel_path = Column(String(512), nullable=True)
    bmodel_rel_path = Column(String(512), nullable=True)
    published_model_id = Column(Integer, nullable=True, index=True)
    business_thresholds_json = Column(Text, nullable=True)
    validation_thresholds_json = Column(Text, nullable=True)
    threshold_binding_json = Column(Text, nullable=True)
    artifact_manifest_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    quality_gate_json = Column(Text, nullable=True)
    auto_training_plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=True, index=True)
    auto_training_round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=True, unique=True)
    calibration_manifest_id = Column(Integer, nullable=True)
    execution_signature = Column(String(64), nullable=True, unique=True)
    pipeline_origin = Column(String(32), nullable=True, index=True)
    # Short handoff lease owned by the automatic dispatcher. This is kept
    # separate from the compiler/worker status so two schedulers cannot start
    # the same export before the worker changes status to PROCESSING.
    queue_lease_owner = Column(String(128), nullable=True, index=True)
    queue_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # Execution lease is separate from dispatcher handoff lease. The token
    # fences stale compiler workers after a takeover.
    execution_lease_owner = Column(String(128), nullable=True, index=True)
    execution_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    execution_fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    # AICM publication has its own cross-process claim.  It must not reuse
    # compiler execution leases because publication can outlive compilation.
    publish_status = Column(String(32), nullable=True, index=True)
    publish_job_id = Column(String(64), nullable=True, index=True)
    publish_lease_owner = Column(String(128), nullable=True, index=True)
    publish_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    publish_fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    # 独立 BM1688 Worker 的动作路由：export=PT→ONNX→BModel，recompile=已有
    # ONNX→BModel。历史记录为空时按 export 兼容处理。
    worker_action = Column(String(32), nullable=True, default="export", index=True)
    compile_log = Column(Text, nullable=True)
    quality_report = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
