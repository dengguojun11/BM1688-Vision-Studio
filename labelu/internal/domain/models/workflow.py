from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from labelu.internal.common.db import Base


class WorkflowInstance(Base):
    __tablename__ = "workflow_instance"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_workflow_instance_idempotency"),
        Index("idx_workflow_instance_project_status", "project_id", "status"),
        Index("idx_workflow_instance_scope_status", "scope_key", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # ``project_id`` is retained for historical workflows only. New 7GAI
    # evolution workflows are global and use ``scope_key`` for stable
    # ownership/query semantics instead of a fake default project.
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    scope_key = Column(String(160), nullable=False, default="global", index=True)
    workflow_type = Column(String(48), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    idempotency_key = Column(String(160), nullable=False)
    payload_json = Column(Text, nullable=True)
    current_step = Column(String(96), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    # Governance metadata is explicit so operators can assign ownership and
    # prove which input was approved for execution.  Nullable keeps upgrades
    # compatible with historical workflow rows; new rows are populated by the
    # application service.
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    approver_id = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True, index=True)
    input_snapshot_json = Column(Text, nullable=True)
    callback_url = Column(String(512), nullable=True)
    callback_signature = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    steps = relationship(
        "WorkflowStep", cascade="all, delete-orphan", order_by="WorkflowStep.sequence"
    )
    events = relationship(
        "WorkflowEvent", cascade="all, delete-orphan", order_by="WorkflowEvent.id"
    )
    outbox = relationship(
        "WorkflowOutbox", cascade="all, delete-orphan", order_by="WorkflowOutbox.id"
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_step"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_key", name="uq_workflow_step_key"),
        Index("idx_workflow_step_dispatch", "status", "available_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(
        Integer,
        ForeignKey("workflow_instance.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_key = Column(String(96), nullable=False)
    sequence = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    available_at = Column(DateTime(timezone=True), nullable=True, index=True)
    input_json = Column(Text, nullable=True)
    output_json = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class WorkflowEvent(Base):
    __tablename__ = "workflow_event"
    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(
        Integer,
        ForeignKey("workflow_instance.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(64), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    step_key = Column(String(96), nullable=True)
    payload_json = Column(Text, nullable=True)
    actor_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.now, nullable=False, index=True
    )


class WorkflowOutbox(Base):
    __tablename__ = "workflow_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_workflow_outbox_idempotency"),
        Index("idx_workflow_outbox_dispatch", "status", "available_at", "id"),
        Index("idx_workflow_outbox_lease", "status", "lease_expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(
        Integer,
        ForeignKey("workflow_instance.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(64), nullable=False, index=True)
    aggregate_type = Column(String(64), nullable=False, default="workflow")
    aggregate_id = Column(String(128), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    payload_json = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    available_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)
