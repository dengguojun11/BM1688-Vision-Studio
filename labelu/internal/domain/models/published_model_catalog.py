from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from labelu.internal.common.db import Base


class PublishedModelCatalog(Base):
    __tablename__ = "published_model_catalog"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    public_model_id = Column(String(128), nullable=False, unique=True, index=True)
    aicm_environment_id = Column(String(64), nullable=True, index=True)
    aicm_model_set_id = Column(Integer, nullable=True, index=True)
    export_id = Column(
        Integer, ForeignKey("yolo_bm1688_export.id"), nullable=True, index=True
    )
    yolo_train_job_id = Column(
        Integer, ForeignKey("yolo_train_job.id"), nullable=True, index=True
    )
    task_id = Column(Integer, ForeignKey("task.id"), nullable=True, index=True)
    name = Column(String(256), nullable=False, default="")
    display_name = Column(String(256), nullable=False, default="")
    source_type = Column(
        String(32), nullable=False, default="labelu_publish", index=True
    )
    source_system = Column(String(64), nullable=False, default="aicm", index=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    is_public = Column(Boolean, nullable=False, default=True, index=True)
    download_enabled = Column(Boolean, nullable=False, default=True, index=True)
    availability_status = Column(
        String(32), nullable=False, default="unknown", index=True
    )
    chip_type = Column(String(128), nullable=True)
    algorithm_type = Column(String(64), nullable=True)
    precision = Column(String(16), nullable=True)
    resolution = Column(String(64), nullable=True)
    map50 = Column(String(32), nullable=True)
    labels_json = Column(Text, nullable=True)
    remote_download_url = Column(Text, nullable=True)
    package_rel_path = Column(String(512), nullable=True)
    package_size_bytes = Column(BigInteger, nullable=True)
    package_sha256 = Column(String(64), nullable=True)
    package_md5 = Column(String(32), nullable=True)
    publisher_username = Column(String(128), nullable=True)
    publish_job_id = Column(String(64), nullable=True, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    aliases = relationship(
        "PublishedModelIdentifierAlias",
        back_populates="catalog",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "aicm_environment_id",
            "aicm_model_set_id",
            name="uq_published_model_catalog_aicm_model",
        ),
    )


class PublishedModelIdentifierAlias(Base):
    __tablename__ = "published_model_identifier_alias"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    catalog_id = Column(
        Integer,
        ForeignKey("published_model_catalog.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_system = Column(String(64), nullable=False, index=True)
    external_model_id = Column(String(128), nullable=False, index=True)
    alias_type = Column(String(32), nullable=False, default="third_party")
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )

    catalog = relationship("PublishedModelCatalog", back_populates="aliases")

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_model_id",
            name="uq_published_model_identifier_alias_source_id",
        ),
    )
