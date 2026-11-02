from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from labelu.internal.common.db import Base


class AnnotationReviewEvent(Base):
    """Append-only review transition used for per-reviewer workload."""

    __tablename__ = "annotation_review_event"
    __table_args__ = (
        Index("ix_annotation_review_event_user_created", "user_id", "created_at"),
        Index("ix_annotation_review_event_task_created", "task_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    review_id = Column(
        Integer, ForeignKey("annotation_review.id"), nullable=False, index=True
    )
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, index=True)
