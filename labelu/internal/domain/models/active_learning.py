from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.schema import UniqueConstraint

from labelu.internal.common.db import Base


class FeedbackCluster(Base):
    __tablename__ = "feedback_cluster"
    __table_args__ = (
        UniqueConstraint("scope_key", "cluster_key", name="uq_feedback_cluster_scope_key"),
        Index("idx_feedback_cluster_scope_status", "scope_key", "status", "updated_at"),
        Index("idx_feedback_cluster_project_status", "project_id", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # ``project_id`` is retained for historical rows only. New feedback uses
    # the durable scope key so NULL never participates in an idempotency key.
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True)
    scope_key = Column(String(160), nullable=False, default="global", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    cluster_key = Column(String(128), nullable=False)
    event_type = Column(String(32), nullable=True)
    device_id = Column(String(128), nullable=True)
    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=True, index=True)
    source_key = Column(String(160), nullable=True, index=True)
    sample_count = Column(Integer, nullable=False, default=0)
    average_confidence = Column(Integer, nullable=True)
    priority_score = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="READY", index=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class ActiveLearningBatch(Base):
    __tablename__ = "active_learning_batch"
    __table_args__ = (
        UniqueConstraint("scope_key", "batch_key", name="uq_active_learning_batch_scope_key"),
        Index("idx_active_learning_batch_scope_status", "scope_key", "status", "updated_at"),
        Index("idx_active_learning_batch_project_status", "project_id", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True)
    scope_key = Column(String(160), nullable=False, default="global", index=True)
    batch_key = Column(String(160), nullable=False)
    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=True, index=True)
    source_key = Column(String(160), nullable=True, index=True)
    quota = Column(Integer, nullable=False)
    selected_count = Column(Integer, nullable=False, default=0)
    accepted_count = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="READY", index=True)
    scoring_policy_json = Column(Text, nullable=True)
    workflow_id = Column(Integer, ForeignKey("workflow_instance.id"), nullable=True, index=True)
    dataset_version_id = Column(
        Integer, ForeignKey("dataset_version.id"), nullable=True, index=True
    )
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class ActiveLearningBatchItem(Base):
    __tablename__ = "active_learning_batch_item"
    __table_args__ = (
        UniqueConstraint("batch_id", "hard_example_id", name="uq_active_learning_batch_item"),
        Index("idx_active_learning_batch_item_batch", "batch_id", "rank"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("active_learning_batch.id", ondelete="CASCADE"), nullable=False, index=True)
    hard_example_id = Column(Integer, ForeignKey("edge_hard_example.id"), nullable=False, index=True)
    score = Column(Integer, nullable=False, default=0)
    rank = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
