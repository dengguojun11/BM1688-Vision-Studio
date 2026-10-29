from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import relationship

from labelu.internal.common.db import Base


class ExperimentDatasetGroup(Base):
    """Aggregates immutable third-party packages before a task is created."""

    __tablename__ = "experiment_dataset_group"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    aggregation_key = Column(String(512), nullable=False, unique=True, index=True)
    source_type = Column(String(32), nullable=False, default="third_party")
    source_system = Column(String(64), nullable=True, index=True)
    source_external_task_id = Column(String(128), nullable=True, index=True)
    dataset_type = Column(String(64), nullable=False)
    dataset_date = Column(String(32), nullable=False, index=True)
    event_type = Column(String(128), nullable=False, default="")
    model_id = Column(String(128), nullable=False, default="")
    dataset_category_id = Column(
        Integer,
        ForeignKey("dataset_category.id"),
        nullable=True,
        index=True,
    )
    identity_json = Column(Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False)
    min_sample_count = Column(Integer, nullable=False, default=50)
    declared_sample_count = Column(Integer, nullable=False, default=0)
    valid_sample_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="COLLECTING", index=True)
    formal_task_id = Column(Integer, ForeignKey("task.id"), nullable=True, index=True)
    model_resolution_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    created_task_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    dataset_category = relationship("DatasetCategory", foreign_keys=[dataset_category_id])
