import json
from typing import List, Optional
from sqlalchemy.orm import Session
from labelu.internal.domain.models.export_job import ExportJob, ExportStatus


def create(db: Session, task_id: int, user_id: int, export_type: str, sample_ids: List[int]) -> ExportJob:
    job = ExportJob(
        task_id=task_id,
        created_by=user_id,
        export_type=export_type,
        sample_count=len(sample_ids),
        sample_ids=json.dumps(sample_ids),
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[ExportJob]:
    return db.query(ExportJob).filter(ExportJob.id == job_id).first()


def get_active_label_dataset_import(db: Session, task_id: int) -> Optional[ExportJob]:
    return (
        db.query(ExportJob)
        .filter(
            ExportJob.task_id == task_id,
            ExportJob.export_type == "LABEL_DATASET_IMPORT",
            ExportJob.status.in_([ExportStatus.PENDING.value, ExportStatus.PROCESSING.value]),
        )
        .order_by(ExportJob.id.desc())
        .first()
    )


def update_status(db: Session, job: ExportJob, status: str, **kwargs) -> ExportJob:
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    db.flush()
    db.refresh(job)
    return job


def increment_progress(db: Session, job: ExportJob, *, success: bool) -> ExportJob:
    job.processed_count = (job.processed_count or 0) + 1
    db.flush()
    db.refresh(job)
    return job
