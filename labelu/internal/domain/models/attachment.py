from datetime import datetime

from sqlalchemy.schema import Index
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, BigInteger, Index
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Index

from labelu.internal.common.db import Base


class TaskAttachment(Base):
    __tablename__ = "task_attachment"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    filename = Column(String(256), comment="file name")
    url = Column(String(256), comment="file url")
    path = Column(String(256), comment="file storage path or S3 object key")
    task_id = Column(Integer, ForeignKey("task.id"), index=True)
    data_source_id = Column(
        Integer, ForeignKey("data_source.id"), nullable=True, index=True,
        comment="NULL = local upload, set = imported from external data source",
    )
    media_asset_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="7GAI media asset compatibility projection",
    )
    created_by = Column(Integer, ForeignKey("user.id"), index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), index=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.now, comment="Time a task was created"
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        comment="Last time a task was updated",
    )
    deleted_at = Column(DateTime, index=True, comment="Task delete time")
    # Content identity is optional for legacy rows and populated by new uploads.
    sha256 = Column(String(64), nullable=True, index=True, comment="content SHA-256")
    size_bytes = Column(BigInteger, nullable=True, comment="content size in bytes")
    content_type = Column(String(128), nullable=True, comment="content MIME type")
    source_bucket = Column(String(128), nullable=True, index=True, comment="外部对象来源桶")
    source_object_key = Column(String(1024), nullable=True, comment="外部对象来源键")
    source_version_id = Column(String(256), nullable=True, comment="外部对象版本")
    source_etag = Column(String(256), nullable=True, comment="外部对象 ETag")
    source_sha256 = Column(String(64), nullable=True, index=True, comment="外部对象内容 SHA-256")

    data_source = relationship("DataSource", lazy="joined")

    Index("idx_attachment_id_deleted_at", id, deleted_at)
    Index("idx_attachment_task_sha256", task_id, sha256, deleted_at)
    Index(
        "idx_attachment_task_source_identity",
        task_id,
        source_bucket,
        source_object_key,
        deleted_at,
        mysql_length={"source_object_key": 255},
    )
