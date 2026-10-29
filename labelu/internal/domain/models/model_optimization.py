from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from labelu.internal.common.db import Base


class ModelOptimizationJob(Base):
    """Durable record for one model optimization adapter invocation.

    The job is deliberately separate from ``WorkflowStep`` so an optimizer
    can be retried or inspected without losing workflow lineage.  A local
    adapter may complete synchronously, but it must declare that no binary
    artifact was produced unless a real optimizer created one.
    """

    __tablename__ = "model_optimization_job"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_model_optimization_job_idempotency"),
        Index("idx_model_optimization_job_project_status", "project_id", "status"),
        Index("idx_model_optimization_job_workflow", "workflow_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False, index=True)
    workflow_id = Column(Integer, ForeignKey("workflow_instance.id", ondelete="SET NULL"), nullable=True)
    model_version_id = Column(Integer, ForeignKey("model_version.id"), nullable=False, index=True)
    adapter_key = Column(String(64), nullable=False, default="local_sync")
    idempotency_key = Column(String(192), nullable=False)
    status = Column(String(24), nullable=False, default="QUEUED", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    parameters_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
