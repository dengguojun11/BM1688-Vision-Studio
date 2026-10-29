from typing import Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.sample_bounds_check_job import (
    SampleBoundsCheckJob,
    SampleBoundsCheckStatus,
)


def create(db: Session, task_id: int, user_id: int, sample_count: int) -> SampleBoundsCheckJob:
    job = SampleBoundsCheckJob(
        task_id=task_id,
        created_by=user_id,
        sample_count=sample_count,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[SampleBoundsCheckJob]:
    return db.query(SampleBoundsCheckJob).filter(SampleBoundsCheckJob.id == job_id).first()


def get_active_by_task_id(db: Session, task_id: int) -> Optional[SampleBoundsCheckJob]:
    return (
        db.query(SampleBoundsCheckJob)
        .filter(
            SampleBoundsCheckJob.task_id == task_id,
            SampleBoundsCheckJob.status.in_(
                [
                    SampleBoundsCheckStatus.PENDING.value,
                    SampleBoundsCheckStatus.PROCESSING.value,
                ]
            ),
        )
        .order_by(SampleBoundsCheckJob.id.desc())
        .first()
    )


def update_status(db: Session, job: SampleBoundsCheckJob, status: str, **kwargs) -> SampleBoundsCheckJob:
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    db.flush()
    db.refresh(job)
    return job


def update_progress(
    db: Session,
    job: SampleBoundsCheckJob,
    *,
    normal_delta: int = 0,
    incomplete_delta: int = 0,
    abnormal_delta: int = 0,
    failed_delta: int = 0,
) -> SampleBoundsCheckJob:
    job.processed_count = (job.processed_count or 0) + 1
    job.normal_count = (job.normal_count or 0) + normal_delta
    job.incomplete_count = (job.incomplete_count or 0) + incomplete_delta
    job.abnormal_count = (job.abnormal_count or 0) + abnormal_delta
    job.failed_count = (job.failed_count or 0) + failed_delta
    db.flush()
    db.refresh(job)
    return job
