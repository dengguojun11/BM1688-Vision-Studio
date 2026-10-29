"""7GAI edge Agent identity, capability and OTA control-plane records."""

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

from labelu.internal.common.db import Base


class EdgeAgent(Base):
    __tablename__ = "edge_agent"
    __table_args__ = (
        Index("idx_edge_agent_project_status", "project_id", "status", "updated_at"),
    )

    agent_id = Column(String(128), primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    name = Column(String(160), nullable=False)
    token_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="REGISTERED", index=True)
    agent_version = Column(String(64), nullable=True)
    platform = Column(String(64), nullable=True)
    capabilities_json = Column(Text, nullable=True)
    desired_state_json = Column(Text, nullable=True)
    reported_state_json = Column(Text, nullable=True)
    last_ip = Column(String(64), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class EdgeAgentRelease(Base):
    __tablename__ = "edge_agent_release"
    __table_args__ = (
        UniqueConstraint("release_key", name="uq_edge_agent_release_key"),
        Index("idx_edge_agent_release_status", "status", "created_at"),
        Index("idx_edge_agent_release_project_status", "project_id", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Historical releases may be unscoped; strict deployments reject new ones.
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    release_key = Column(String(160), nullable=False)
    version = Column(String(64), nullable=False)
    artifact_path = Column(String(1024), nullable=False)
    artifact_sha256 = Column(String(64), nullable=False)
    artifact_size = Column(Integer, nullable=False)
    chunk_size = Column(Integer, nullable=False, default=1024 * 1024)
    signature = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="PUBLISHED", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class EdgeAgentDeployment(Base):
    __tablename__ = "edge_agent_deployment"
    __table_args__ = (
        UniqueConstraint("release_id", "agent_id", name="uq_edge_agent_deployment_target"),
        Index("idx_edge_agent_deployment_agent_state", "agent_id", "state", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(Integer, ForeignKey("edge_agent_release.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(128), ForeignKey("edge_agent.agent_id", ondelete="CASCADE"), nullable=False, index=True)
    state = Column(String(32), nullable=False, default="PENDING", index=True)
    downloaded_bytes = Column(Integer, nullable=False, default=0)
    verified_sha256 = Column(String(64), nullable=True)
    active_version = Column(String(64), nullable=True)
    previous_version = Column(String(64), nullable=True)
    failure_reason = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_chunk_index = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
