"""Canonical 7GAI edge deployment records.

These tables are the control-plane view of a deployment.  They deliberately do
not replace ``yolo_board_validation`` or ``edge_model_deployment``: those
records remain adapters for board validation and auto-training rounds.  A
release has one target row per device and observations are append-only samples
reported by an agent.
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship
from sqlalchemy import event

from labelu.internal.common.db import Base


LONGTEXT = Text().with_variant(mysql.LONGTEXT(), "mysql")


class DeploymentRelease(Base):
    __tablename__ = "deployment_release"
    __table_args__ = (
        UniqueConstraint("release_key", name="uq_deployment_release_key"),
        Index("idx_deployment_release_project_status", "project_id", "status", "updated_at"),
        Index("idx_deployment_release_model", "model_version_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False, index=True)
    model_version_id = Column(
        Integer, ForeignKey("model_version.id"), nullable=False, index=True
    )
    release_key = Column(String(160), nullable=False)
    status = Column(String(32), nullable=False, default="DRAFT", index=True)
    target_device_ids_json = Column(LONGTEXT, nullable=False, default="[]")
    gray_ratio = Column(Float, nullable=False, default=0.05)
    observation_seconds = Column(Integer, nullable=False, default=900)
    rollback_model_version_id = Column(
        Integer, ForeignKey("model_version.id"), nullable=True, index=True
    )
    metrics_json = Column(LONGTEXT, nullable=True)
    # Monotonic control-plane generation.  Agents may include the generation
    # they observed; stale reports are rejected by the service CAS check.
    fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )

    targets = relationship(
        "DeploymentTarget", back_populates="release", cascade="all, delete-orphan"
    )
    transition_audits = relationship(
        "DeploymentTransitionAudit", back_populates="release"
    )


class DeploymentTarget(Base):
    __tablename__ = "deployment_target"
    __table_args__ = (
        UniqueConstraint("release_id", "device_id", name="uq_deployment_target_device"),
        Index("idx_deployment_target_release_state", "release_id", "desired_state", "reported_state"),
        Index("idx_deployment_target_device_state", "device_id", "reported_state", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(
        Integer, ForeignKey("deployment_release.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = Column(String(128), nullable=False)
    desired_state = Column(String(32), nullable=False, default="PENDING")
    reported_state = Column(String(32), nullable=False, default="UNKNOWN")
    target_bundle_sha256 = Column(String(64), nullable=False)
    reported_bundle_sha256 = Column(String(64), nullable=True)
    agent_version = Column(String(64), nullable=True)
    capabilities_json = Column(LONGTEXT, nullable=True)
    metrics_json = Column(LONGTEXT, nullable=True)
    fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )

    release = relationship("DeploymentRelease", back_populates="targets")
    observations = relationship(
        "DeploymentObservation", back_populates="target", cascade="all, delete-orphan"
    )
    transition_audits = relationship(
        "DeploymentTransitionAudit", back_populates="target"
    )


class DeploymentObservation(Base):
    __tablename__ = "deployment_observation"
    __table_args__ = (
        UniqueConstraint("target_id", "observation_key", name="uq_deployment_observation_key"),
        Index("idx_deployment_observation_target_time", "target_id", "sampled_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(
        Integer, ForeignKey("deployment_target.id", ondelete="CASCADE"), nullable=False, index=True
    )
    observation_key = Column(String(160), nullable=False)
    status = Column(String(24), nullable=False, default="HEALTHY")
    fps = Column(Float, nullable=True)
    latency_p95_ms = Column(Float, nullable=True)
    latency_p99_ms = Column(Float, nullable=True)
    cpu_percent = Column(Float, nullable=True)
    npu_percent = Column(Float, nullable=True)
    memory_percent = Column(Float, nullable=True)
    temperature_c = Column(Float, nullable=True)
    metrics_json = Column(LONGTEXT, nullable=True)
    error_message = Column(Text, nullable=True)
    sampled_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)

    target = relationship("DeploymentTarget", back_populates="observations")


class DeploymentTransitionAudit(Base):
    """Immutable audit event for every deployment control-plane transition.

    ``metrics_json`` on :class:`DeploymentRelease` remains a compatibility
    read model, but this table is the authoritative append-only event log.
    Both release-level user actions and target-level Agent/observation events
    use the same schema so operators can reconstruct a rollout consistently.
    """

    __tablename__ = "deployment_transition_audit"
    __table_args__ = (
        # A client supplied idempotency key is unique when present.  SQLite
        # and MySQL both allow multiple NULL values for this constraint.
        UniqueConstraint("idempotency_key", name="uq_deployment_transition_audit_idempotency"),
        Index("idx_deployment_transition_audit_release_time", "release_id", "created_at"),
        Index("idx_deployment_transition_audit_target_time", "target_id", "created_at"),
        Index("idx_deployment_transition_audit_actor_time", "actor_type", "actor_id", "created_at"),
        Index("idx_deployment_transition_audit_operation_time", "operation", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(
        Integer, ForeignKey("deployment_release.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id = Column(
        Integer, ForeignKey("deployment_target.id", ondelete="CASCADE"), nullable=True, index=True
    )
    operation = Column(String(64), nullable=False)
    actor_type = Column(String(24), nullable=False)
    actor_id = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    agent_id = Column(String(128), nullable=True, index=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=True)
    result = Column(String(24), nullable=False, default="APPLIED")
    reason = Column(Text, nullable=True)
    request_id = Column(String(128), nullable=True, index=True)
    correlation_id = Column(String(128), nullable=True, index=True)
    idempotency_key = Column(String(160), nullable=True)
    fencing_token = Column(Integer, nullable=True)
    before_snapshot_json = Column(LONGTEXT, nullable=True)
    after_snapshot_json = Column(LONGTEXT, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, index=True)

    release = relationship("DeploymentRelease", back_populates="transition_audits")
    target = relationship("DeploymentTarget", back_populates="transition_audits")

    # Keep friendly names for callers using the language in the architecture
    # document while retaining the workflow module's from/to naming convention.
    @property
    def before_status(self):
        return self.from_status

    @before_status.setter
    def before_status(self, value):
        self.from_status = value

    @property
    def after_status(self):
        return self.to_status

    @after_status.setter
    def after_status(self, value):
        self.to_status = value


@event.listens_for(DeploymentTransitionAudit, "before_update")
@event.listens_for(DeploymentTransitionAudit, "before_delete")
def _prevent_deployment_transition_audit_mutation(_mapper, _connection, _target) -> None:
    """The deployment event log is append-only at the ORM boundary."""

    raise ValueError("deployment transition audits are immutable")
