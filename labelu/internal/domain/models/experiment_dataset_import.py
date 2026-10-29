from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Index, UniqueConstraint

from labelu.internal.common.db import Base


class ExperimentDatasetImport(Base):
    __tablename__ = "experiment_dataset_import"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    package_id = Column(String(128), nullable=False, index=True)
    source_type = Column(String(32), nullable=False, default="internal", index=True)
    source_system = Column(String(64), nullable=True, index=True)
    source_external_task_id = Column(String(128), nullable=True, index=True)
    source_external_package_id = Column(String(128), nullable=True, index=True)
    source_integration_id = Column(Integer, nullable=True, index=True)
    dataset_type = Column(String(64), nullable=False)
    dataset_date = Column(String(32), nullable=False)
    event_type = Column(String(128), nullable=False, default="")
    model_id = Column(String(128), nullable=False, default="")
    dataset_category_id = Column(
        Integer,
        ForeignKey("dataset_category.id"),
        nullable=True,
        index=True,
    )
    target_signature = Column(String(64), nullable=False, default="")
    source_payload_json = Column(Text, nullable=False)
    aggregation_key = Column(String(512), nullable=True, index=True)
    aggregation_group_id = Column(
        Integer,
        ForeignKey("experiment_dataset_group.id"),
        nullable=True,
        index=True,
    )
    metadata_json = Column(Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True)
    status = Column(String(32), nullable=False, default="PENDING")
    # S3 回流必须由独立 Worker 持久化领取；不能依赖 Web 进程临时线程。
    worker_lease_owner = Column(String(128), nullable=True, index=True)
    worker_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    worker_fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    formal_task_ids_json = Column(Text, nullable=False)
    draft_task_id = Column(Integer, ForeignKey("task.id"), nullable=True, index=True)
    declared_sample_count = Column(Integer, nullable=False, default=0)
    formal_sample_count = Column(Integer, nullable=False, default=0)
    draft_sample_count = Column(Integer, nullable=False, default=0)
    abnormal_sample_count = Column(Integer, nullable=False, default=0)
    valid_sample_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime(timezone=True), index=True, nullable=True)

    dataset_category = relationship("DatasetCategory", foreign_keys=[dataset_category_id])

    __table_args__ = (
        UniqueConstraint("package_id", name="uq_experiment_dataset_import_package_id"),
        Index("idx_experiment_dataset_import_status_deleted", "status", "deleted_at"),
        Index(
            "idx_experiment_dataset_import_worker_scan",
            "status",
            "worker_lease_expires_at",
            "id",
        ),
    )
