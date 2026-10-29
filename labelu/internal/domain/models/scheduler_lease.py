"""Database-backed leases used to elect a single scheduler leader.

The lease row is deliberately small and generic.  Work items keep their own
leases; this table only coordinates process-level schedulers.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from labelu.internal.common.db import Base


class SchedulerLease(Base):
    __tablename__ = "scheduler_lease"

    # A stable name (for example ``auto_training_watchdog``) gives each
    # scheduler role one row and avoids process-local leader state.
    lease_name = Column(String(96), primary_key=True)
    lease_owner = Column(String(128), nullable=True, index=True)
    fencing_token = Column(Integer, nullable=False, default=0)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now, nullable=False
    )
