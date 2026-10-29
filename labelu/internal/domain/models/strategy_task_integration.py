from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Index, UniqueConstraint

from labelu.internal.common.db import Base


class StrategyTaskIntegration(Base):
    __tablename__ = "strategy_task_integration"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    external_task_id = Column(String(128), nullable=False, index=True)
    labelu_task_id = Column(Integer, ForeignKey("task.id"), nullable=True, index=True)
    datasource_id = Column(
        Integer, ForeignKey("data_source.id"), nullable=True, index=True
    )
    dataset_category_id = Column(
        Integer,
        ForeignKey("dataset_category.id"),
        nullable=True,
        index=True,
    )
    schema_version = Column(String(32), nullable=False, default="v1")
    task_name = Column(String(128), nullable=False)
    task_type = Column(String(64), nullable=False)
    source_type = Column(String(32), nullable=False, default="third_party", index=True)
    source_system = Column(
        String(64), nullable=False, default="strategy_evolution", index=True
    )
    source_external_package_id = Column(String(128), nullable=True, index=True)
    source_package_id = Column(String(128), nullable=True, index=True)
    dataset_s3_uri = Column(String(1024), nullable=False)
    dataset_image_prefix = Column(String(512), nullable=False)
    metadata_key = Column(String(512), nullable=False)
    bucket = Column(String(256), nullable=False)
    prefix = Column(String(512), nullable=False, default="")
    labeling_required = Column(Boolean, nullable=False, default=True)
    labeling_media_type = Column(String(32), nullable=True)
    labeling_annotation_type = Column(String(64), nullable=True)
    labeling_labels_json = Column(Text, nullable=True)
    target_json = Column(Text, nullable=True)
    experiment_json = Column(Text, nullable=True)
    request_payload_json = Column(Text, nullable=True)
    request_sha256 = Column(String(64), nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)
    import_status = Column(String(32), nullable=False, default="PENDING")
    imported_sample_count = Column(Integer, nullable=False, default=0)
    imported_pre_annotation_count = Column(Integer, nullable=False, default=0)
    data_window_json = Column(Text, nullable=True)
    platform_status = Column(String(32), nullable=False, default="accepted")
    platform_progress_json = Column(Text, nullable=True)
    platform_result_json = Column(Text, nullable=True)
    platform_error_json = Column(Text, nullable=True)
    platform_callback_event_id = Column(String(128), nullable=True)
    platform_finished_at = Column(String(64), nullable=True)
    automation_source = Column(String(64), nullable=True)
    automation_stage = Column(String(64), nullable=True)
    automation_status = Column(String(32), nullable=True)
    automation_result_json = Column(Text, nullable=True)
    automation_attempts = Column(Integer, nullable=False, default=0)
    automation_lease_owner = Column(String(128), nullable=True)
    automation_lease_expires_at = Column(
        DateTime(timezone=True), nullable=True, index=True
    )
    outbound_callback_url = Column(String(1024), nullable=True)
    outbound_callback_status = Column(String(32), nullable=True)
    outbound_callback_attempts = Column(Integer, nullable=False, default=0)
    outbound_callback_event_id = Column(String(128), nullable=True)
    outbound_callback_next_retry_at = Column(
        DateTime(timezone=True), nullable=True, index=True
    )
    outbound_callback_last_error = Column(Text, nullable=True)
    source_train_job_id = Column(Integer, nullable=True, index=True)
    source_export_id = Column(Integer, nullable=True, index=True)
    derived_train_job_id = Column(Integer, nullable=True, index=True)
    derived_export_id = Column(Integer, nullable=True, index=True)
    derived_edge_validation_job_id = Column(Integer, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), index=True, nullable=True)

    task = relationship("Task", foreign_keys=[labelu_task_id])
    datasource = relationship("DataSource", foreign_keys=[datasource_id])
    dataset_category = relationship(
        "DatasetCategory", foreign_keys=[dataset_category_id]
    )
    owner = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])

    __table_args__ = (
        UniqueConstraint(
            "external_task_id", name="uq_strategy_task_integration_external_task_id"
        ),
        Index(
            "idx_strategy_task_integration_task_deleted", "labelu_task_id", "deleted_at"
        ),
    )


class StrategyPlatformCallbackReceipt(Base):
    __tablename__ = "strategy_platform_callback_receipt"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_task_integration_id = Column(
        Integer,
        ForeignKey("strategy_task_integration.id", ondelete="CASCADE"),
        nullable=False,
    )
    callback_event_id = Column(String(128), nullable=False)
    platform_status = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)

    __table_args__ = (
        Index(
            "ix_strategy_callback_receipt_task",
            "strategy_task_integration_id",
        ),
        UniqueConstraint(
            "strategy_task_integration_id",
            "callback_event_id",
            name="uq_strategy_platform_callback_receipt_task_event",
        ),
    )
