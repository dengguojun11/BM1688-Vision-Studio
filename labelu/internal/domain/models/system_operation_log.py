from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from labelu.internal.common.db import Base


class SystemOperationLog(Base):
    """Simplified operation log for the system log screens.

    The list only renders ``action``/``summary``; the full request evidence is
    stored in ``detail`` and returned by the detail endpoint.
    """

    __tablename__ = "system_operation_log"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    category = Column(String(16), nullable=False, index=True, comment="USER or SYSTEM")
    action = Column(String(160), nullable=False, index=True)
    summary = Column(String(512), nullable=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(128), nullable=True)
    source = Column(String(32), nullable=True)
    request_id = Column(String(64), nullable=True, index=True)
    method = Column(String(16), nullable=False)
    path = Column(String(512), nullable=False, index=True)
    status_code = Column(Integer, nullable=True, index=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False, index=True)

    __table_args__ = (
        Index("ix_system_operation_log_category_created", "category", "created_at"),
        Index("ix_system_operation_log_user_created", "user_id", "created_at"),
    )
