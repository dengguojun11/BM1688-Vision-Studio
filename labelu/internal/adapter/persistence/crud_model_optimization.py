from sqlalchemy.orm import Session

from labelu.internal.domain.models.model_optimization import ModelOptimizationJob


def get(db: Session, job_id: int) -> ModelOptimizationJob | None:
    return db.query(ModelOptimizationJob).filter(ModelOptimizationJob.id == int(job_id)).first()


def get_by_key(db: Session, idempotency_key: str) -> ModelOptimizationJob | None:
    return db.query(ModelOptimizationJob).filter(ModelOptimizationJob.idempotency_key == str(idempotency_key)).first()


def create(db: Session, row: ModelOptimizationJob) -> ModelOptimizationJob:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row
