from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.schema import UniqueConstraint

from labelu.internal.common.db import Base


class Organization(Base):
    """Tenant-level ownership boundary for 7GAI projects."""

    __tablename__ = "organization"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_organization_owner_name"),
        Index("idx_organization_owner_status", "owner_id", "status", "deleted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class Scene(Base):
    """Project-scoped scene taxonomy used by ingestion and evolution scoring."""

    __tablename__ = "scene"
    __table_args__ = (
        UniqueConstraint("project_id", "scene_key", name="uq_scene_project_key"),
        Index("idx_scene_project_status", "project_id", "status", "deleted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True)
    scene_key = Column(String(128), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    metadata_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
