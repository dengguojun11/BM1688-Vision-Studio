from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from labelu.internal.common.db import Base


class MediaUploadSession(Base):
    """Resumable upload manifest; chunks are stored in a temporary file."""

    __tablename__ = "media_upload_session"
    __table_args__ = (
        Index("idx_media_upload_task_status", "task_id", "status", "updated_at"),
        Index("idx_media_upload_sha256", "task_id", "sha256"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, unique=True)
    task_id = Column(Integer, ForeignKey("task.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(256), nullable=False)
    total_size = Column(Integer, nullable=False)
    chunk_size = Column(Integer, nullable=False)
    total_chunks = Column(Integer, nullable=False)
    received_chunks_json = Column(Text, nullable=False, default="[]")
    sha256 = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False, default="UPLOADING")
    scan_status = Column(String(24), nullable=False, default="PENDING")
    scan_provider = Column(String(64), nullable=True)
    scan_signature = Column(String(256), nullable=True)
    scan_error = Column(Text, nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    quarantined_at = Column(DateTime(timezone=True), nullable=True)
    quarantine_path = Column(String(512), nullable=True)
    quarantine_until = Column(DateTime(timezone=True), nullable=True, index=True)
    attachment_id = Column(Integer, ForeignKey("task_attachment.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
