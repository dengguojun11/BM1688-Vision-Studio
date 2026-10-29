from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.schema import UniqueConstraint

from labelu.internal.common.db import Base


class Project(Base):
    """Project boundary for the 7GAI platform.

    Existing tasks remain independent until the project backfill migration is
    approved; this table is therefore intentionally additive in B1.
    """

    __tablename__ = "project"
    __table_args__ = (
        Index("idx_project_owner_status", "owner_id", "status", "deleted_at"),
        Index("idx_project_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organization.id"), nullable=True, index=True)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="DRAFT", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class ProjectMember(Base):
    """Project membership and the minimum RBAC role needed by B1."""

    __tablename__ = "project_member"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member_project_user"),
        Index("idx_project_member_user_status", "user_id", "status"),
        Index("idx_project_member_project_status", "project_id", "status"),
    )

    project_id = Column(
        Integer, ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    user_id = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    role = Column(String(24), nullable=False, default="member")
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    invited_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
