from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.schema import UniqueConstraint

from labelu.internal.common.db import Base


class AnnotationReview(Base):
    """Review record for one immutable annotation snapshot.

    The formal annotation, pre-annotation and review decision remain separate
    records.  ``annotation_sha256`` binds a decision to the exact snapshot the
    reviewer saw and prevents stale review results from being reused.
    """

    __tablename__ = "annotation_review"
    __table_args__ = (
        UniqueConstraint(
            "sample_id",
            "annotation_sha256",
            "source_type",
            name="uq_annotation_review_snapshot",
        ),
        Index(
            "idx_annotation_review_task_status",
            "task_id",
            "review_status",
            "updated_at",
        ),
        Index(
            "idx_annotation_review_sample_status",
            "sample_id",
            "review_status",
            "updated_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    sample_id = Column(
        Integer, ForeignKey("task_sample.id"), nullable=False, index=True
    )
    annotation_sha256 = Column(String(64), nullable=False, index=True)
    source_type = Column(String(32), nullable=False, default="FORMAL")
    review_status = Column(String(24), nullable=False, default="PENDING", index=True)
    review_reason = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    arbitrator_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    arbitrated_at = Column(DateTime(timezone=True), nullable=True)
    arbitration_decision = Column(String(24), nullable=True)
    arbitration_reason = Column(Text, nullable=True)
    invalidated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidation_reason = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )
