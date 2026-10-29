from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Index

from labelu.internal.common.db import Base


class ThirdPartyCallAudit(Base):
    """Sanitized audit record for one inbound third-party HTTP call."""

    __tablename__ = "third_party_call_audit"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("project.id"),
        nullable=True,
        index=True,
        comment="Resolved tenant boundary; nullable for pre-migration audit records",
    )
    request_id = Column(String(64), nullable=False, unique=True, index=True)
    source_system = Column(String(64), nullable=False, default="unknown", index=True)
    operation = Column(String(64), nullable=False, index=True)
    request_method = Column(String(16), nullable=False)
    request_path = Column(String(512), nullable=False, index=True)
    schema_version = Column(String(32), nullable=True)
    external_task_id = Column(String(128), nullable=True, index=True)
    external_package_id = Column(String(128), nullable=True, index=True)
    request_sha256 = Column(String(64), nullable=False)
    request_bytes = Column(Integer, nullable=False, default=0)
    response_status = Column(Integer, nullable=False, index=True)
    outcome = Column(String(32), nullable=False, index=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    client_ip = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    error_summary = Column(Text, nullable=True)
    received_at = Column(
        DateTime(timezone=True), default=datetime.now, nullable=False, index=True
    )

    project = relationship("Project", foreign_keys=[project_id])

    __table_args__ = (
        Index(
            "ix_third_party_call_source_received",
            "source_system",
            "received_at",
        ),
        Index(
            "ix_third_party_call_operation_received",
            "operation",
            "received_at",
        ),
    )
