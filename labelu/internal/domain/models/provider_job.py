"""Provider execution and immutable prediction snapshot models."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from labelu.internal.common.db import Base


class ProviderJob(Base):
    __tablename__ = "provider_job"
    __table_args__ = (
        UniqueConstraint("created_by", "request_signature", name="uq_provider_job_creator_request"),
        Index("idx_provider_job_creator_status", "created_by", "status", "updated_at"),
        Index("idx_provider_job_provider", "provider_key", "provider_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Historical project scope is nullable compatibility storage only.
    project_id = Column(Integer, ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True)
    provider_key = Column(String(128), nullable=False, index=True)
    provider_version = Column(String(64), nullable=False)
    model_version_id = Column(Integer, nullable=True, index=True)
    input_asset_ids_json = Column(Text, nullable=False, default="[]")
    contract_version = Column(String(64), nullable=False, default="prediction.v1")
    config_json = Column(Text, nullable=True)
    request_signature = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="QUEUED", index=True)
    total_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)


class PredictionSet(Base):
    __tablename__ = "prediction_set"
    __table_args__ = (
        UniqueConstraint("provider_job_id", "version", name="uq_prediction_set_job_version"),
        Index("idx_prediction_set_creator_review", "created_by", "review_status", "created_at"),
        Index("idx_prediction_set_sample", "sample_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Historical project scope is nullable compatibility storage only.
    project_id = Column(Integer, ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True)
    provider_job_id = Column(Integer, ForeignKey("provider_job.id", ondelete="CASCADE"), nullable=False, index=True)
    sample_id = Column(Integer, nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    annotation_contract_version = Column(String(64), nullable=False, default="annotation_contract.v1")
    prediction_schema_version = Column(String(64), nullable=False, default="prediction.v1")
    payload_uri = Column(String(1024), nullable=True)
    payload_sha256 = Column(String(64), nullable=True)
    result_manifest_uri = Column(String(1024), nullable=True)
    result_sha256 = Column(String(64), nullable=True)
    # Optional inline payload used by the review/legacy projection bridge.
    # URI-backed results remain the canonical large-result path; keeping the
    # small review payload separate avoids overloading metrics_json.
    payload_json = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="COMPLETED", index=True)
    review_status = Column(String(24), nullable=False, default="PENDING", index=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_reason = Column(Text, nullable=True)
    source = Column(String(16), nullable=False, default="AI")
    item_count = Column(Integer, nullable=False, default=0)
    metrics_json = Column(Text, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
