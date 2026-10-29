"""Project-scoped rule templates, immutable versions and device bindings."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, inspect

from labelu.internal.common.db import Base


class RuleTemplate(Base):
    __tablename__ = "rule_template"
    __table_args__ = (
        UniqueConstraint("project_id", "template_key", name="uq_rule_template_project_key"),
        Index("idx_rule_template_project_status", "project_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    template_key = Column(String(128), nullable=False)
    name = Column(String(160), nullable=False)
    algorithm_key = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class RuleVersion(Base):
    __tablename__ = "rule_version"
    __table_args__ = (
        UniqueConstraint("template_id", "version_no", name="uq_rule_version_template_version"),
        Index("idx_rule_version_template_status", "template_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("rule_template.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    schema_version = Column(String(32), nullable=False, default="v1")
    config_json = Column(Text, nullable=False)
    config_sha256 = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="DRAFT", index=True)
    signing_payload_sha256 = Column(String(64), nullable=True)
    signature_algorithm = Column(String(32), nullable=True)
    signature_key_id = Column(String(128), nullable=True)
    signature = Column(Text, nullable=True)
    server_signature_status = Column(String(32), nullable=False, default="NOT_PROVIDED")
    device_verification_status = Column(String(32), nullable=False, default="UNVERIFIED")
    signature_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class RuleBinding(Base):
    __tablename__ = "rule_binding"
    __table_args__ = (
        UniqueConstraint("device_id", "active_key", name="uq_rule_binding_device_active"),
        Index("idx_rule_binding_project_status", "project_id", "status"),
        Index("idx_rule_binding_device_status", "device_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(String(128), nullable=False, index=True)
    rule_version_id = Column(Integer, ForeignKey("rule_version.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="INACTIVE", index=True)
    active_key = Column(String(128), nullable=True)
    device_verification_status = Column(String(32), nullable=False, default="UNVERIFIED")
    device_report_sequence = Column(Integer, nullable=False, default=0)
    last_device_report_id = Column(String(160), nullable=True)
    device_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class RuleBindingAudit(Base):
    __tablename__ = "rule_binding_audit"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_rule_binding_audit_idempotency"),
        Index("idx_rule_binding_audit_project_time", "project_id", "created_at"),
        Index("idx_rule_binding_audit_binding_time", "binding_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    binding_id = Column(Integer, ForeignKey("rule_binding.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(String(128), nullable=False, index=True)
    operation = Column(String(32), nullable=False)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    actor_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    reason = Column(Text, nullable=True)
    idempotency_key = Column(String(160), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class RuleVersionAudit(Base):
    """Append-only audit trail for rule version publication transitions."""

    __tablename__ = "rule_version_audit"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_rule_version_audit_idempotency"),
        Index("idx_rule_version_audit_project_time", "project_id", "created_at"),
        Index("idx_rule_version_audit_version_time", "version_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    version_id = Column(Integer, ForeignKey("rule_version.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = Column(Integer, ForeignKey("rule_template.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    operation = Column(String(32), nullable=False)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    actor_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    reason = Column(Text, nullable=True)
    idempotency_key = Column(String(160), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class RuleDeviceSignatureReport(Base):
    """Append-only evidence reported by an authenticated edge Agent."""

    __tablename__ = "rule_device_signature_report"
    __table_args__ = (
        UniqueConstraint("report_id", name="uq_rule_device_signature_report_id"),
        UniqueConstraint(
            "rule_binding_id",
            "sequence",
            name="uq_rule_device_signature_report_binding_sequence",
        ),
        Index(
            "idx_rule_device_signature_report_project_time",
            "project_id",
            "received_at",
        ),
        Index(
            "idx_rule_device_signature_report_device_time",
            "device_id",
            "received_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String(160), nullable=False)
    sequence = Column(Integer, nullable=False)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(String(128), ForeignKey("edge_agent.agent_id", ondelete="CASCADE"), nullable=False, index=True)
    rule_binding_id = Column(Integer, ForeignKey("rule_binding.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_version_id = Column(Integer, ForeignKey("rule_version.id", ondelete="RESTRICT"), nullable=False, index=True)
    payload_sha256 = Column(String(64), nullable=False)
    key_id = Column(String(128), nullable=False)
    key_state = Column(String(24), nullable=False)
    reported_status = Column(String(24), nullable=False)
    effective_status = Column(String(32), nullable=False)
    failure_code = Column(String(128), nullable=True)
    reason = Column(Text, nullable=True)
    reported_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


@event.listens_for(RuleVersion, "before_update")
def _protect_rule_version(_mapper, _connection, target):
    state = inspect(target)
    immutable_fields = (
        "template_id", "version_no", "schema_version", "config_json", "config_sha256",
        "signing_payload_sha256", "signature_algorithm", "signature_key_id", "signature",
        "server_signature_status", "device_verification_status", "signature_verified_at",
        "created_by", "created_at",
    )
    if any(state.attrs[name].history.has_changes() for name in immutable_fields):
        raise ValueError("rule version content is immutable")


@event.listens_for(RuleBindingAudit, "before_update")
@event.listens_for(RuleBindingAudit, "before_delete")
def _immutable_audit(_mapper, _connection, _target):
    raise ValueError("rule binding audits are immutable")


@event.listens_for(RuleVersionAudit, "before_update")
@event.listens_for(RuleVersionAudit, "before_delete")
def _immutable_version_audit(_mapper, _connection, _target):
    raise ValueError("rule version audits are immutable")


@event.listens_for(RuleDeviceSignatureReport, "before_update")
@event.listens_for(RuleDeviceSignatureReport, "before_delete")
def _immutable_device_signature_report(_mapper, _connection, _target):
    raise ValueError("rule device signature reports are immutable")
