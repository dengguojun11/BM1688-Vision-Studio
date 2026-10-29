from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from labelu.internal.common.db import Base


class InferenceEvent(Base):
    __tablename__ = "inference_event"
    __table_args__ = (
        UniqueConstraint("project_id", "event_key", name="uq_inference_event_key"),
        UniqueConstraint("scope_key", "event_key", name="uq_inference_event_scope_key"),
        Index("idx_inference_event_project_created", "project_id", "created_at"),
        Index("idx_inference_event_scope_created", "scope_key", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # ``project_id`` is historical compatibility only. New edge feedback is
    # global and is isolated by ``scope_key`` plus creator permissions.
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    scope_key = Column(String(160), nullable=False, default="global", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    device_id = Column(String(128), nullable=False, index=True)
    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=True, index=True)
    event_key = Column(String(160), nullable=False)
    event_type = Column(String(32), nullable=False, default="LOW_CONFIDENCE")
    confidence = Column(Integer, nullable=True)
    asset_uri = Column(String(1024), nullable=True)
    asset_sha256 = Column(String(64), nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False, index=True)


class EdgeHardExample(Base):
    __tablename__ = "edge_hard_example"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_edge_hard_example_fingerprint"),
        UniqueConstraint("scope_key", "fingerprint", name="uq_edge_hard_example_scope_fingerprint"),
        Index("idx_edge_hard_example_queue", "project_id", "review_status", "training_status"),
        Index("idx_edge_hard_example_scope_queue", "scope_key", "review_status", "training_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    scope_key = Column(String(160), nullable=False, default="global", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    inference_event_id = Column(Integer, ForeignKey("inference_event.id"), nullable=False, index=True)
    device_id = Column(String(128), nullable=False, index=True)
    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=True, index=True)
    fingerprint = Column(String(128), nullable=False)
    asset_uri = Column(String(1024), nullable=True)
    confidence = Column(Integer, nullable=True)
    hard_example_type = Column(String(32), nullable=False)
    review_status = Column(String(24), nullable=False, default="PENDING", index=True)
    review_reason = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    training_status = Column(String(24), nullable=False, default="NOT_INCLUDED", index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now, nullable=False)
