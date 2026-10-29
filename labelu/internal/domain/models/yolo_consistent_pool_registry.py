from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from labelu.internal.common.db import Base


class YoloConsistentPoolRegistry(Base):
    """Database-level ownership of one pool per dataset category and label set."""

    __tablename__ = "yolo_consistent_pool_registry"
    __table_args__ = (
        UniqueConstraint(
            "category_scope_key",
            "label_set_signature",
            name="uq_yolo_consistent_pool_registry_scope_signature",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    category_scope_key = Column(
        String(64), nullable=False, default="uncategorized", index=True
    )
    dataset_category_id = Column(
        Integer, ForeignKey("dataset_category.id"), nullable=True, index=True
    )
    label_set_signature = Column(String(64), nullable=False, index=True)
    task_id = Column(
        Integer, ForeignKey("task.id"), nullable=True, unique=True, index=True
    )
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
