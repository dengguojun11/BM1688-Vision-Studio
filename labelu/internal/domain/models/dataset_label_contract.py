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

from labelu.internal.common.db import Base


class DatasetLabelContract(Base):
    """Immutable label contract defining the classes of a logical dataset."""

    __tablename__ = "dataset_label_contract"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "revision",
            name="uq_dataset_label_contract_revision",
        ),
        Index(
            "idx_dataset_label_contract_dataset_status",
            "dataset_id",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    dataset_id = Column(
        Integer,
        ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    annotation_kind = Column(String(32), nullable=False, default="bbox")
    label_ids_json = Column(Text, nullable=False, default="[]")
    labels_snapshot_json = Column(Text, nullable=False, default="[]")
    label_set_signature = Column(String(64), nullable=False, index=True)
    class_order_signature = Column(String(64), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="DRAFT", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    frozen_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    frozen_at = Column(DateTime(timezone=True), nullable=True)


class DatasetLabelContractItem(Base):
    """Ordered label membership for a dataset contract."""

    __tablename__ = "dataset_label_contract_item"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "label_id",
            name="uq_dataset_label_contract_item_label",
        ),
        Index(
            "idx_dataset_label_contract_item_contract_order",
            "contract_id",
            "sort_order",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    dataset_id = Column(
        Integer,
        ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_id = Column(
        Integer,
        ForeignKey("dataset_label_contract.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label_id = Column(
        Integer, ForeignKey("annotation_label.id"), nullable=False, index=True
    )
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
