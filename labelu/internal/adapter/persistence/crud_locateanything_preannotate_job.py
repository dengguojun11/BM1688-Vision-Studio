from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_

from sqlalchemy.orm import Session

from labelu.internal.domain.models.locateanything_preannotate_job import LocateAnythingPreannotateJob


def create(
    db: Session,
    *,
    task_id: int,
    user_id: int,
    sample_count: int,
    provider: str = "locateanything",
    payload_json: str | None = None,
) -> LocateAnythingPreannotateJob:
    job = LocateAnythingPreannotateJob(
        task_id=task_id,
        created_by=user_id,
        sample_count=sample_count,
        provider=(provider or "locateanything").strip().lower(),
        payload_json=payload_json,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[LocateAnythingPreannotateJob]:
    return db.query(LocateAnythingPreannotateJob).filter(LocateAnythingPreannotateJob.id == job_id).first()


def get_active_by_task_id(db: Session, task_id: int, provider: str | None = None) -> Optional[LocateAnythingPreannotateJob]:
    query = (
        db.query(LocateAnythingPreannotateJob)
        .filter(
            LocateAnythingPreannotateJob.task_id == task_id,
            LocateAnythingPreannotateJob.status.in_(["PENDING", "PROCESSING"]),
        )
    )
    if provider:
        query = query.filter(LocateAnythingPreannotateJob.provider == provider)
    return query.order_by(LocateAnythingPreannotateJob.id.desc()).first()


def list_runnable(db: Session, *, provider: str | None = None, now: datetime | None = None, limit: int = 1):
    now = now or datetime.now()
    query = db.query(LocateAnythingPreannotateJob).filter(
        or_(
            LocateAnythingPreannotateJob.status == "PENDING",
            and_(
                LocateAnythingPreannotateJob.status == "PROCESSING",
                or_(
                    LocateAnythingPreannotateJob.lease_expires_at.is_(None),
                    LocateAnythingPreannotateJob.lease_expires_at < now,
                ),
            ),
        )
    )
    if provider:
        query = query.filter(LocateAnythingPreannotateJob.provider == provider)
    return query.order_by(LocateAnythingPreannotateJob.id.asc()).limit(max(int(limit), 1)).all()


def claim_with_token(db: Session, *, job_id: int, lease_owner: str, lease_seconds: int, provider: str | None = None, now: datetime | None = None) -> int | None:
    now = now or datetime.now()
    expires = now + timedelta(seconds=max(int(lease_seconds), 30))
    filters = [
        LocateAnythingPreannotateJob.id == int(job_id),
        or_(
            LocateAnythingPreannotateJob.status == "PENDING",
            and_(
                LocateAnythingPreannotateJob.status == "PROCESSING",
                or_(
                    LocateAnythingPreannotateJob.lease_expires_at.is_(None),
                    LocateAnythingPreannotateJob.lease_expires_at < now,
                ),
            ),
        ),
    ]
    if provider:
        filters.append(LocateAnythingPreannotateJob.provider == provider)
    matched = db.query(LocateAnythingPreannotateJob).filter(*filters).update(
        {
            LocateAnythingPreannotateJob.status: "PROCESSING",
            LocateAnythingPreannotateJob.lease_owner: lease_owner,
            LocateAnythingPreannotateJob.lease_expires_at: expires,
            LocateAnythingPreannotateJob.attempt_count: LocateAnythingPreannotateJob.attempt_count + 1,
            LocateAnythingPreannotateJob.lease_fencing_token: LocateAnythingPreannotateJob.lease_fencing_token + 1,
            LocateAnythingPreannotateJob.updated_at: now,
        }, synchronize_session=False
    )
    db.flush()
    if not matched:
        return None
    return int(db.query(LocateAnythingPreannotateJob.lease_fencing_token).filter(
        LocateAnythingPreannotateJob.id == int(job_id), LocateAnythingPreannotateJob.lease_owner == lease_owner
    ).scalar() or 0)


def increment_progress_fenced(db: Session, *, job_id: int, lease_owner: str, lease_fencing_token: int, success: bool) -> bool:
    values = {
        LocateAnythingPreannotateJob.processed_count: LocateAnythingPreannotateJob.processed_count + 1,
        LocateAnythingPreannotateJob.updated_at: datetime.now(),
    }
    field = LocateAnythingPreannotateJob.success_count if success else LocateAnythingPreannotateJob.failed_count
    values[field] = field + 1
    changed = db.query(LocateAnythingPreannotateJob).filter(
        LocateAnythingPreannotateJob.id == int(job_id), LocateAnythingPreannotateJob.status == "PROCESSING",
        LocateAnythingPreannotateJob.lease_owner == lease_owner,
        LocateAnythingPreannotateJob.lease_fencing_token == int(lease_fencing_token),
    ).update(values, synchronize_session=False)
    db.flush()
    return bool(changed)


def renew_fenced(db: Session, *, job_id: int, lease_owner: str, lease_fencing_token: int, lease_seconds: int, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    changed = db.query(LocateAnythingPreannotateJob).filter(
        LocateAnythingPreannotateJob.id == int(job_id),
        LocateAnythingPreannotateJob.status == "PROCESSING",
        LocateAnythingPreannotateJob.lease_owner == lease_owner,
        LocateAnythingPreannotateJob.lease_fencing_token == int(lease_fencing_token),
    ).update({
        LocateAnythingPreannotateJob.lease_expires_at: now + timedelta(seconds=max(int(lease_seconds), 30)),
        LocateAnythingPreannotateJob.updated_at: now,
    }, synchronize_session=False)
    db.flush()
    return bool(changed)


def finish_fenced(db: Session, *, job_id: int, lease_owner: str, lease_fencing_token: int, status: str, error_message: str | None = None) -> bool:
    changed = db.query(LocateAnythingPreannotateJob).filter(
        LocateAnythingPreannotateJob.id == int(job_id), LocateAnythingPreannotateJob.status == "PROCESSING",
        LocateAnythingPreannotateJob.lease_owner == lease_owner,
        LocateAnythingPreannotateJob.lease_fencing_token == int(lease_fencing_token),
    ).update({
        LocateAnythingPreannotateJob.status: status,
        LocateAnythingPreannotateJob.error_message: error_message,
        LocateAnythingPreannotateJob.lease_owner: None,
        LocateAnythingPreannotateJob.lease_expires_at: None,
        LocateAnythingPreannotateJob.updated_at: datetime.now(),
    }, synchronize_session=False)
    db.flush()
    return bool(changed)


def update_status(db: Session, job: LocateAnythingPreannotateJob, status: str, **kwargs) -> LocateAnythingPreannotateJob:
    job.status = status
    for key, value in kwargs.items():
        setattr(job, key, value)
    db.flush()
    db.refresh(job)
    return job


def increment_progress(db: Session, job: LocateAnythingPreannotateJob, success: bool) -> LocateAnythingPreannotateJob:
    job.processed_count = (job.processed_count or 0) + 1
    if success:
        job.success_count = (job.success_count or 0) + 1
    else:
        job.failed_count = (job.failed_count or 0) + 1
    db.flush()
    db.refresh(job)
    return job
