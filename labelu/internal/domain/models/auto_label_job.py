from enum import Enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from labelu.internal.common.db import Base


class AutoLabelStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL_COMPLETED = "PARTIAL_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AutoLabelJob(Base):
    __tablename__ = "auto_label_job"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), index=True)
    created_by = Column(Integer, ForeignKey("user.id"))
    status = Column(String(32), default=AutoLabelStatus.PENDING.value)
    sample_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    filter_by_labels = Column(Boolean, default=True)
    job_kind = Column(String(32), nullable=False, default="MANUAL_BATCH", index=True)
    source_hint = Column(String(128), nullable=True)
    source_key = Column(String(128), nullable=True, unique=True, index=True)
    payload_json = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # Monotonically increasing execution generation.  A reclaimed job gets a
    # new token so an old worker cannot commit a late result after its lease
    # expires (owner-only checks are insufficient during a process pause).
    lease_fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
