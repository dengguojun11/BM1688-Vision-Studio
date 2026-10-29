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
from sqlalchemy.orm import relationship

from labelu.internal.common.db import Base


class ModelVersion(Base):
    """Canonical internal model lineage; external publish catalogs remain adapters."""

    __tablename__ = "model_version"
    __table_args__ = (
        UniqueConstraint("algorithm_id", "version_no", name="uq_model_version_number"),
        UniqueConstraint("model_key", name="uq_model_version_key"),
        UniqueConstraint("training_job_id", name="uq_model_version_training_job"),
        Index("idx_model_version_algorithm_status", "algorithm_id", "status"),
        Index("idx_model_version_creator_status", "created_by", "status"),
        Index("idx_model_version_dataset", "dataset_version_id"),
        Index("idx_model_version_logical_dataset", "dataset_id"),
        Index("idx_model_version_series_variant", "series_id", "variant_id"),
        Index(
            "idx_model_version_branch_version",
            "series_id",
            "variant_id",
            "branch_version_no",
        ),
        Index("idx_model_version_branch_key", "branch_key"),
        Index(
            "idx_model_version_parent_training_job",
            "parent_training_job_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    algorithm_id = Column(
        Integer, ForeignKey("algorithm_definition.id"), nullable=False, index=True
    )
    dataset_id = Column(Integer, ForeignKey("dataset.id"), nullable=True)
    dataset_version_id = Column(
        Integer, ForeignKey("dataset_version.id"), nullable=True, index=True
    )
    training_job_id = Column(
        Integer, ForeignKey("yolo_train_job.id"), nullable=True, index=True
    )
    parent_model_version_id = Column(
        Integer, ForeignKey("model_version.id"), nullable=True, index=True
    )
    series_id = Column(
        Integer,
        ForeignKey("model_series.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    variant_id = Column(
        Integer,
        ForeignKey("model_variant.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    inheritance_mode = Column(String(32), nullable=True)
    # The branch identity and immutable contract are copied from the training
    # job at registration time.  Keeping them on the canonical model row means
    # model-center responses remain self-contained even if the job is archived
    # or a legacy worker omitted its JSON snapshot.
    branch_key = Column(String(64), nullable=True, index=True)
    selection_contract_sha256 = Column(String(64), nullable=True, index=True)
    selection_contract_json = Column(Text, nullable=True)
    parent_training_job_id = Column(Integer, nullable=True, index=True)
    published_catalog_id = Column(
        Integer, ForeignKey("published_model_catalog.id"), nullable=True, index=True
    )
    model_key = Column(String(160), nullable=False)
    # ``version_no`` is the historical algorithm-global number used by
    # download keys and older integrations.  ``branch_version_no`` is the
    # user-facing sequence within one model-series branch; it is nullable so
    # legacy/imported rows remain readable without fabricated lineage.
    branch_version_no = Column(Integer, nullable=True, index=True)
    version_no = Column(Integer, nullable=False)
    display_name = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, default="CANDIDATE", index=True)
    chip_type = Column(String(64), nullable=True)
    precision = Column(String(16), nullable=True)
    input_contract_json = Column(Text, nullable=True)
    output_contract_json = Column(Text, nullable=True)
    preprocess_contract_json = Column(Text, nullable=True)
    postprocess_contract_json = Column(Text, nullable=True)
    metrics_json = Column(Text, nullable=True)
    manifest_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    published_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )
    published_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)

    artifacts = relationship(
        "ModelArtifact", back_populates="model_version", cascade="all, delete-orphan"
    )
    quality_evidence = relationship(
        "ModelQualityEvidence",
        back_populates="model_version",
        cascade="all, delete-orphan",
    )
    series = relationship("ModelSeries", foreign_keys=[series_id], lazy="joined")
    variant = relationship("ModelVariant", foreign_keys=[variant_id], lazy="joined")


class ModelArtifact(Base):
    __tablename__ = "model_artifact"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id", "artifact_type", "sha256", name="uq_model_artifact_hash"
        ),
        Index("idx_model_artifact_model_status", "model_version_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_version_id = Column(
        Integer,
        ForeignKey("model_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_type = Column(String(32), nullable=False)  # pt/onnx/bmodel/manifest
    relative_path = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    chip_type = Column(String(64), nullable=True)
    precision = Column(String(16), nullable=True)
    status = Column(String(24), nullable=False, default="READY", index=True)
    metadata_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)

    model_version = relationship("ModelVersion", back_populates="artifacts")


class ModelQualityEvidence(Base):
    __tablename__ = "model_quality_evidence"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id",
            "gate_type",
            "revision",
            name="uq_model_quality_gate_revision",
        ),
        Index(
            "idx_model_quality_model_gate", "model_version_id", "gate_type", "status"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_version_id = Column(
        Integer,
        ForeignKey("model_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gate_type = Column(
        String(32), nullable=False
    )  # training/generalization/conversion/edge/release
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    metrics_json = Column(Text, nullable=True)
    evidence_uri = Column(String(512), nullable=True)
    evidence_sha256 = Column(String(64), nullable=True)
    evaluator = Column(String(128), nullable=True)
    reason = Column(Text, nullable=True)
    evaluated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)

    model_version = relationship("ModelVersion", back_populates="quality_evidence")
