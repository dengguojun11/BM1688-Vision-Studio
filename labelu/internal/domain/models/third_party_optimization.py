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


class OptimizationRequest(Base):
    """Third-party business request for one model optimization / threshold / scene task."""

    __tablename__ = "optimization_request"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_request_id",
            name="uq_optimization_request_external",
        ),
        Index("ix_optimization_request_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    external_request_id = Column(String(160), nullable=False, index=True)
    source_system = Column(String(64), nullable=False, default="third_party", index=True)
    client_id = Column(String(128), nullable=True, index=True)
    request_type = Column(String(48), nullable=False, index=True)
    external_model_id = Column(String(160), nullable=True, index=True)
    labelu_model_id = Column(Integer, nullable=True, index=True)
    export_id = Column(Integer, nullable=True, index=True)
    event_label = Column(String(160), nullable=True, index=True)
    scene_json = Column(Text, nullable=True)
    package_ids_json = Column(Text, nullable=True)
    event_time_from = Column(DateTime(timezone=True), nullable=True, index=True)
    event_time_to = Column(DateTime(timezone=True), nullable=True, index=True)
    require_complete = Column(Boolean, nullable=False, default=True)
    allow_partial = Column(Boolean, nullable=False, default=False)
    min_sample_count = Column(Integer, nullable=False, default=50)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    stage = Column(String(48), nullable=False, default="RECEIVED", index=True)
    progress_json = Column(Text, nullable=True)
    coverage_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error_json = Column(Text, nullable=True)
    callback_url = Column(String(1024), nullable=True)
    callback_event_id = Column(String(160), nullable=True)
    strategy_external_task_id = Column(String(160), nullable=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflow_instance.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now, nullable=False
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class OptimizationCycle(Base):
    """One accumulation/training cycle for a request."""

    __tablename__ = "optimization_cycle"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    request_id = Column(
        Integer,
        ForeignKey("optimization_request.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    aggregation_key = Column(String(512), nullable=False, unique=True)
    model_id = Column(String(160), nullable=True)
    event_label = Column(String(160), nullable=True)
    scene_json = Column(Text, nullable=True)
    aggregation_group_id = Column(Integer, nullable=True, index=True)
    status = Column(String(32), nullable=False, default="COLLECTING", index=True)
    min_sample_count = Column(Integer, nullable=False, default=50)
    declared_sample_count = Column(Integer, nullable=False, default=0)
    valid_sample_count = Column(Integer, nullable=False, default=0)
    task_ids_json = Column(Text, nullable=True)
    dataset_version_id = Column(Integer, nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now, nullable=False
    )


class ThirdPartyEventImage(Base):
    """Mapping from an external event image to a request/cycle/task/sample."""

    __tablename__ = "third_party_event_image"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "external_image_id",
            name="uq_third_party_event_image_request_external",
        ),
        Index("ix_third_party_event_image_time", "event_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    request_id = Column(
        Integer,
        ForeignKey("optimization_request.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cycle_id = Column(
        Integer,
        ForeignKey("optimization_cycle.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_system = Column(String(64), nullable=False, default="third_party", index=True)
    package_id = Column(String(160), nullable=True, index=True)
    external_image_id = Column(String(160), nullable=False, index=True)
    external_event_id = Column(String(160), nullable=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    sample_id = Column(Integer, nullable=True, index=True)
    event_label = Column(String(160), nullable=True, index=True)
    event_time = Column(DateTime(timezone=True), nullable=True, index=True)
    scene_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)


class IntegrationPublishDecision(Base):
    """Publish decision and manual-approval evidence for one request."""

    __tablename__ = "integration_publish_decision"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    request_id = Column(
        Integer,
        ForeignKey("optimization_request.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    candidate_model_id = Column(Integer, nullable=True, index=True)
    baseline_model_id = Column(Integer, nullable=True, index=True)
    candidate_export_id = Column(Integer, nullable=True)
    metrics_json = Column(Text, nullable=True)
    auto_publish_eligible = Column(Boolean, nullable=False, default=False)
    decision = Column(String(32), nullable=False, default="PENDING", index=True)
    reason = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    decided_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    published_model_id = Column(Integer, nullable=True)
    aicm_model_set_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now, nullable=False
    )
