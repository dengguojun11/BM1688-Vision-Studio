from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.schema import UniqueConstraint

from labelu.internal.common.db import Base


class AlgorithmGroup(Base):
    """Global product grouping for trainable algorithms.

    Groups are deliberately not project-scoped.  Project membership controls
    access to data and jobs; the registry describes platform capabilities.
    """

    __tablename__ = "algorithm_group"
    __table_args__ = (
        UniqueConstraint("group_key", name="uq_algorithm_group_key"),
        Index("idx_algorithm_group_status_sort", "status", "sort_order"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_key = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )


class AlgorithmDefinition(Base):
    """Version-independent definition of a trainable/deployable capability."""

    __tablename__ = "algorithm_definition"
    __table_args__ = (
        UniqueConstraint("algorithm_key", name="uq_algorithm_definition_key"),
        Index("idx_algorithm_definition_group_status", "algorithm_group_id", "status"),
        Index("idx_algorithm_definition_task_type", "task_type", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    algorithm_key = Column(String(128), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    task_type = Column(String(64), nullable=False, index=True)
    algorithm_group_id = Column(
        Integer, ForeignKey("algorithm_group.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    label_set_signature = Column(String(64), nullable=True, index=True)
    # Explicit governance fields.  They are nullable for rolling upgrades;
    # service responses fall back to metadata_json for legacy rows.
    algorithm_kind = Column(String(24), nullable=True, index=True)
    runtime_adapter = Column(String(128), nullable=True)
    rule_schema_json = Column(Text, nullable=True)
    supported_formats_json = Column(Text, nullable=True)
    min_samples_json = Column(Text, nullable=True)
    quality_gate_json = Column(Text, nullable=True)
    risk_level = Column(String(24), nullable=True, index=True)
    annotation_contract_version = Column(String(64), nullable=True)
    required_tools_json = Column(Text, nullable=False, default="[]")
    required_attributes_json = Column(Text, nullable=False, default="[]")
    training_adapter = Column(String(128), nullable=False)
    evaluation_adapter = Column(String(128), nullable=False)
    supported_chips_json = Column(Text, nullable=False, default="[]")
    default_recipe = Column(String(128), nullable=True)
    input_contract_json = Column(Text, nullable=True)
    output_contract_json = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    group = relationship("AlgorithmGroup", lazy="joined", viewonly=True)
