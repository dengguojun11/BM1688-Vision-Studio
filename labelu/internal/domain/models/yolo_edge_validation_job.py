from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from labelu.internal.common.db import Base


class YoloEdgeValidationJob(Base):
    __tablename__ = "yolo_edge_validation_job"
    __table_args__ = (
        Index(
            "ix_yolo_edge_validation_job_dispatch_scan",
            "status",
            "queued_at",
            "id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    export_id = Column(
        Integer, ForeignKey("yolo_bm1688_export.id"), nullable=False, index=True
    )
    source_type = Column(String(32), nullable=False, default="internal", index=True)
    source_system = Column(String(64), nullable=True, index=True)
    source_external_task_id = Column(String(128), nullable=True, index=True)
    source_external_package_id = Column(String(128), nullable=True, index=True)
    source_integration_id = Column(Integer, nullable=True, index=True)

    status = Column(String(32), nullable=False, default="PENDING", index=True)
    mode = Column(String(32), nullable=False, default="run_only")
    threshold_source = Column(String(64), nullable=True)
    auto_training_plan_id = Column(
        Integer, ForeignKey("auto_training_plan.id"), nullable=True, index=True
    )
    auto_training_round_id = Column(
        Integer, ForeignKey("auto_training_round.id"), nullable=True, unique=True
    )
    deployment_id = Column(Integer, nullable=True)
    execution_signature = Column(String(64), nullable=True, unique=True)
    pipeline_origin = Column(String(32), nullable=True, index=True)

    edge_device_id = Column(String(64), nullable=True, index=True)
    edge_device_name = Column(String(64), nullable=True)
    edge_host = Column(String(256), nullable=True)
    edge_port = Column(Integer, nullable=True)
    edge_user = Column(String(128), nullable=True)
    queue_reason = Column(String(64), nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=True, index=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Dispatcher handoff lease; ``lease_expires_at`` above belongs to the
    # actual board assignment and must not be reused for scheduler claims.
    queue_lease_owner = Column(String(128), nullable=True, index=True)
    queue_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # Execution lease is separate from queue and board assignment leases.
    execution_lease_owner = Column(String(128), nullable=True, index=True)
    execution_lease_expires_at = Column(
        DateTime(timezone=True), nullable=True, index=True
    )
    execution_fencing_token = Column(
        Integer, nullable=False, default=0, server_default="0"
    )

    sample_ids_json = Column(Text, nullable=True)
    thresholds_json = Column(Text, nullable=True)
    ai_thresholds_json = Column(Text, nullable=True)

    image_count = Column(Integer, nullable=False, default=0)
    labeled_image_count = Column(Integer, nullable=False, default=0)
    unlabeled_image_count = Column(Integer, nullable=False, default=0)

    progress_json = Column(Text, nullable=True, comment="手动边端验证实时进度 JSON")
    result_json = Column(Text, nullable=True)
    report_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
