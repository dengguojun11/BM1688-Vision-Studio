"""7GAI data-ingestion and media-asset boundary models.

These models are deliberately additive. ``DataSource`` and ``TaskAttachment``
remain compatibility storage/legacy task views; new ingestion code is scoped
by creator and task access. ``project_id`` remains nullable only as a historical
database compatibility column and is not part of the一期 API contract.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import synonym

from labelu.internal.common.db import Base


class IngestionJob(Base):
    __tablename__ = "ingestion_job"
    __table_args__ = (
        UniqueConstraint(
            "created_by", "idempotency_key", name="uq_ingestion_job_creator_idempotency"
        ),
        Index("idx_ingestion_job_creator_status", "created_by", "status", "updated_at"),
        Index("idx_ingestion_job_source", "source_type", "source_connection_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Historical project_id is nullable and never populated by new一期 writes.
    project_id = Column(Integer, ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True)
    # Scene is introduced independently; keeping this nullable and unlinked
    # allows old databases to upgrade before the scene table exists.
    scene_id = Column(Integer, nullable=True, index=True)
    source_connection_id = Column(Integer, ForeignKey("data_source.id"), nullable=True, index=True)
    source_type = Column(String(32), nullable=False, default="UPLOAD", index=True)
    idempotency_key = Column(String(256), nullable=False)
    source_snapshot_json = Column(Text, nullable=True)
    selection_json = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="QUEUED", index=True)
    manifest_sha256 = Column(String(64), nullable=True)
    total_count = Column(Integer, nullable=False, default=0)
    accepted_count = Column(Integer, nullable=False, default=0)
    quarantined_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)


class MediaAsset(Base):
    __tablename__ = "media_asset"
    __table_args__ = (
        UniqueConstraint(
            "created_by", "sha256", "media_type", name="uq_media_asset_creator_sha_media"
        ),
        Index("idx_media_asset_creator_scan", "created_by", "scan_status", "lifecycle_status"),
        Index("idx_media_asset_ingestion", "ingestion_job_id", "created_at"),
        # scene_id/project_id remain historical compatibility columns; new
        # ingestion queries are creator/task scoped.
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Historical project_id is nullable and never populated by new一期 writes.
    project_id = Column(Integer, ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True)
    scene_id = Column(Integer, nullable=True, index=True)
    ingestion_job_id = Column(
        Integer, ForeignKey("ingestion_job.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # ``object_uri`` is the contract name in the overview ADR.  ``asset_uri``
    # is exposed as a synonym for the detailed ADR spelling and compatibility
    # callers, while retaining one physical value.
    object_uri = Column(String(1024), nullable=False)
    asset_uri = synonym("object_uri")
    source_object_key = Column(String(1024), nullable=True)
    media_type = Column(String(32), nullable=False, index=True)
    content_type = Column(String(128), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=False, index=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_ms = Column(BigInteger, nullable=True)
    frame_index = Column(Integer, nullable=True)
    scan_status = Column(String(24), nullable=False, default="PENDING", index=True)
    scan_provider = Column(String(64), nullable=True)
    scan_signature = Column(String(256), nullable=True)
    scan_error = Column(Text, nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    quality_status = Column(String(24), nullable=False, default="PENDING", index=True)
    lifecycle_status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    metadata_json = Column(Text, nullable=True)
    # Optional compatibility projection back to an existing task attachment.
    # Kept as a nullable compatibility reference without a database FK: the
    # legacy attachment table may be upgraded independently and linking both
    # tables with FKs would create an unnecessary DDL cycle.
    attachment_id = Column(Integer, nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
