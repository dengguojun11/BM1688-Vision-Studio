from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from labelu.internal.common.db import Base


class TaskAnnotationContract(Base):
    __tablename__ = "task_annotation_contract"
    __table_args__ = (
        UniqueConstraint("task_id", "revision", name="uq_task_annotation_contract_revision"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    annotation_kind = Column(String(32), nullable=False, default="bbox")
    label_ids_json = Column(Text, nullable=False, default="[]")
    labels_snapshot_json = Column(Text, nullable=False, default="[]")
    label_set_signature = Column(String(64), nullable=False, index=True)
    class_order_signature = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    source_preset_id = Column(Integer, ForeignKey("label_preset.id"), nullable=True)
    parent_contract_id = Column(Integer, ForeignKey("task_annotation_contract.id"), nullable=True)
    dataset_label_contract_id = Column(
        Integer,
        ForeignKey("dataset_label_contract.id"),
        nullable=True,
        index=True,
    )
    label_scope_mode = Column(String(16), nullable=False, default="FULL")
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)


class TaskAnnotationLabel(Base):
    __tablename__ = "task_annotation_label"
    __table_args__ = (
        UniqueConstraint("contract_id", "label_id", name="uq_task_annotation_label_contract_label"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    contract_id = Column(Integer, ForeignKey("task_annotation_contract.id"), nullable=False, index=True)
    label_id = Column(Integer, ForeignKey("annotation_label.id"), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="active", index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)


class SampleAnnotationScope(Base):
    __tablename__ = "sample_annotation_scope"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False, unique=True, index=True)
    contract_id = Column(Integer, ForeignKey("task_annotation_contract.id"), nullable=False, index=True)
    completion_status = Column(String(32), nullable=False, default="unprocessed", index=True)
    explicit_negative = Column(Boolean, nullable=False, default=False)
    negative_label_ids_json = Column(Text, nullable=False, default="[]")
    annotation_sha256 = Column(String(64), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)


class SampleAnnotationLabelIndex(Base):
    """Search index of distinct rectangle labels present in each sample."""

    __tablename__ = "sample_annotation_label_index"
    __table_args__ = (
        UniqueConstraint(
            "sample_id",
            "label_value",
            name="uq_sample_annotation_label_index_sample_value",
        ),
        Index(
            "ix_sample_annotation_label_index_value_task_sample",
            "label_value",
            "task_id",
            "sample_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    label_value = Column(String(128), nullable=False)
