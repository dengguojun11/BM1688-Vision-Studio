from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class LocateAnythingPreannotateJob(Base):
    """Async job that writes LocateAnything rectTool pre-annotations."""

    __tablename__ = "locateanything_preannotate_job"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), index=True)
    created_by = Column(Integer, ForeignKey("user.id"))
    status = Column(String(32), default="PENDING")
    sample_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    # This table is shared by LocateAnything, VLX-Seek and Rex-Omni.  Keep
    # provider and immutable worker input on the row so a restarted worker can
    # dispatch the exact original request without consulting the web process.
    provider = Column(String(32), nullable=False, default="locateanything", index=True)
    payload_json = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lease_fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
    )
