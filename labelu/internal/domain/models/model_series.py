"""Domain records for model-series and branch lineage.

The first training implementation grouped runs by task and algorithm.  That
projection is not enough once one model can be split by a label/global-tag
selection.  These tables deliberately keep the branch contract immutable and
nullable links on legacy rows so the migration can be rolled out safely.
"""

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
from sqlalchemy.orm import relationship

from labelu.internal.common.db import Base


class ModelSeries(Base):
    """A long-lived business model family (for example, road detection)."""

    __tablename__ = "model_series"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_model_series_owner_name"),
        Index("idx_model_series_owner_status", "owner_id", "status"),
        Index("idx_model_series_family_status", "model_family", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    model_family = Column(String(128), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    variants = relationship(
        "ModelVariant",
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="ModelVariant.id",
    )


class ModelVariant(Base):
    """A stable branch identified by its normalized selection contract."""

    __tablename__ = "model_variant"
    __table_args__ = (
        UniqueConstraint("series_id", "branch_key", name="uq_model_variant_series_branch"),
        Index("idx_model_variant_series_status", "series_id", "status"),
        Index("idx_model_variant_branch_key", "branch_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    series_id = Column(
        Integer,
        ForeignKey("model_series.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(128), nullable=False)
    branch_key = Column(String(64), nullable=False)
    selection_contract_json = Column(Text, nullable=False, default="{}")
    selection_contract_sha256 = Column(String(64), nullable=False, index=True)
    class_contract_json = Column(Text, nullable=True)
    recipe_json = Column(Text, nullable=True)
    # This is intentionally a nullable scalar instead of a database FK.  A
    # variant may be created before its first model version, while
    # ``model_version.variant_id`` points back to this table.
    parent_model_version_id = Column(Integer, nullable=True, index=True)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    series = relationship("ModelSeries", back_populates="variants")

