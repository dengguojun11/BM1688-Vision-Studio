from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from labelu.internal.common.db import Base


class UserActivitySession(Base):
    """One browser session used for online/presence statistics."""

    __tablename__ = "user_activity_session"
    __table_args__ = (
        UniqueConstraint("user_id", "session_id", name="uq_user_activity_session"),
        Index("ix_user_activity_session_last_seen", "last_seen_at"),
        Index("ix_user_activity_session_user_last_seen", "user_id", "last_seen_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    session_id = Column(String(128), nullable=False, index=True)
    login_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, index=True)
    last_path = Column(String(512), nullable=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    logout_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
