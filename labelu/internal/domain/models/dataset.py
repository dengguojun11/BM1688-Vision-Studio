from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import foreign, relationship

from labelu.internal.common.db import Base
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract


class Dataset(Base):
    """Logical dataset owned by one platform user."""

    __tablename__ = "dataset"
    __table_args__ = (
        Index("idx_dataset_status_updated", "status", "updated_at", "deleted_at"),
        Index("idx_dataset_owner_updated", "owner_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Kept for historical project-bound datasets; new evolution datasets may
    # leave this unset and are governed by owner/source lineage instead.
    # 历史项目关联只读兼容；一期全局数据集不再建立对已退役 project
    # 控制面的新外键依赖。生产历史库保留原列和原数据，由迁移/审计读取。
    project_id = Column(Integer, nullable=True, index=True)
    dataset_category_id = Column(
        Integer, ForeignKey("dataset_category.id"), nullable=True, index=True
    )
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    dataset_label_contract_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="Current dataset label contract",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    dataset_category = relationship("DatasetCategory", lazy="joined")
    label_contract = relationship(
        "DatasetLabelContract",
        # ``dataset_label_contract.dataset_id`` points back to this table,
        # while this nullable pointer identifies the current contract.  The
        # pointer intentionally has no database FK so contract creation does
        # not create a circular DDL dependency; make the ORM join explicit.
        primaryjoin=lambda: foreign(Dataset.dataset_label_contract_id)
        == DatasetLabelContract.id,
        foreign_keys=lambda: [Dataset.dataset_label_contract_id],
        lazy="joined",
    )


class DatasetVersion(Base):
    """Immutable-on-freeze dataset manifest and label contract snapshot."""

    __tablename__ = "dataset_version"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no", name="uq_dataset_version_number"),
        UniqueConstraint("manifest_sha256", name="uq_dataset_version_manifest_sha"),
        Index("idx_dataset_version_dataset_status", "dataset_id", "status"),
        Index("idx_dataset_version_manifest_sha", "manifest_sha256"),
        Index("idx_dataset_version_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 历史项目关联只读兼容；一期新版本通过 dataset_id 和创建者血缘归属。
    project_id = Column(Integer, nullable=True, index=True)
    dataset_id = Column(
        Integer, ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False
    )
    dataset_category_id = Column(
        Integer, ForeignKey("dataset_category.id"), nullable=True, index=True
    )
    version_no = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="INGESTING", index=True)
    manifest_rel_path = Column(String(512), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    label_set_signature = Column(String(64), nullable=True)
    class_order_signature = Column(String(64), nullable=True)
    sample_count = Column(Integer, nullable=False, default=0)
    quality_status = Column(String(32), nullable=True)
    # Dataset-version review is separate from the technical validation above.
    # A version must be explicitly approved before it can be frozen.
    review_status = Column(String(24), nullable=True, default="PENDING", index=True)
    review_comment = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(Text, nullable=True)
    dataset_category_snapshot_json = Column(Text, nullable=True)
    dataset_label_contract_id = Column(
        Integer,
        ForeignKey("dataset_label_contract.id"),
        nullable=True,
        index=True,
    )
    dataset_contract_revision = Column(Integer, nullable=True)
    labels_snapshot_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    frozen_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)

    dataset_category = relationship("DatasetCategory", lazy="joined")
    dataset_label_contract = relationship(
        "DatasetLabelContract",
        foreign_keys=[dataset_label_contract_id],
        lazy="joined",
    )
