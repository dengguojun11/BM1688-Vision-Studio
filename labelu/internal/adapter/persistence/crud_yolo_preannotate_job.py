from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.yolo_preannotate_job import YoloPreannotateJob


def create(
    db: Session,
    *,
    task_id: int,
    user_id: int,
    sample_count: int,
    model_kind: str,
    model_id: int,
    task_contract_id: int | None = None,
    task_contract_revision: int | None = None,
    labels_snapshot_json: str | None = None,
    label_set_signature: str | None = None,
    payload_json: str | None = None,
) -> YoloPreannotateJob:
    job = YoloPreannotateJob(
        task_id=task_id,
        created_by=user_id,
        sample_count=sample_count,
        model_kind=model_kind,
        model_id=model_id,
        task_contract_id=task_contract_id,
        task_contract_revision=task_contract_revision,
        labels_snapshot_json=labels_snapshot_json,
        label_set_signature=label_set_signature,
        payload_json=payload_json,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[YoloPreannotateJob]:
    return db.query(YoloPreannotateJob).filter(YoloPreannotateJob.id == job_id).first()


def get_active_by_task_id(db: Session, task_id: int) -> Optional[YoloPreannotateJob]:
    return (
        db.query(YoloPreannotateJob)
        .filter(
            YoloPreannotateJob.task_id == task_id,
            YoloPreannotateJob.status.in_(["PENDING", "PROCESSING"]),
        )
        .order_by(YoloPreannotateJob.id.desc())
        .first()
    )


def update_status(db: Session, job: YoloPreannotateJob, status: str, **kwargs) -> YoloPreannotateJob:
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    db.flush()
    db.refresh(job)
    return job


def list_runnable_jobs(
    db: Session, *, now: datetime | None = None, limit: int = 20
) -> list[YoloPreannotateJob]:
    """Return pending jobs and expired processing jobs for a worker scan."""
    now = now or datetime.now()
    return (
        db.query(YoloPreannotateJob)
        .filter(
            or_(
                YoloPreannotateJob.status == "PENDING",
                and_(
                    YoloPreannotateJob.status == "PROCESSING",
                    or_(
                        YoloPreannotateJob.lease_expires_at.is_(None),
                        YoloPreannotateJob.lease_expires_at < now,
                    ),
                ),
            )
        )
        .order_by(YoloPreannotateJob.id.asc())
        .limit(max(int(limit), 1))
        .all()
    )


def claim_with_token(
    db: Session,
    *,
    job_id: int,
    lease_owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> int | None:
    """Atomically claim a job and return its new fencing generation."""
    now = now or datetime.now()
    expiry = now + timedelta(seconds=max(int(lease_seconds), 30))
    matched = (
        db.query(YoloPreannotateJob)
        .filter(
            YoloPreannotateJob.id == int(job_id),
            or_(
                YoloPreannotateJob.status == "PENDING",
                and_(
                    YoloPreannotateJob.status == "PROCESSING",
                    or_(
                        YoloPreannotateJob.lease_expires_at.is_(None),
                        YoloPreannotateJob.lease_expires_at < now,
                    ),
                ),
            ),
        )
        .update(
            {
                YoloPreannotateJob.status: "PROCESSING",
                YoloPreannotateJob.lease_owner: lease_owner,
                YoloPreannotateJob.lease_expires_at: expiry,
                YoloPreannotateJob.lease_fencing_token: YoloPreannotateJob.lease_fencing_token + 1,
                YoloPreannotateJob.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    if not matched:
        return None
    token = (
        db.query(YoloPreannotateJob.lease_fencing_token)
        .filter(
            YoloPreannotateJob.id == int(job_id),
            YoloPreannotateJob.lease_owner == lease_owner,
        )
        .scalar()
    )
    return int(token or 0)


def renew_lease(
    db: Session,
    *,
    job_id: int,
    lease_owner: str,
    fencing_token: int,
    lease_seconds: int,
) -> bool:
    now = datetime.now()
    matched = (
        db.query(YoloPreannotateJob)
        .filter(
            YoloPreannotateJob.id == int(job_id),
            YoloPreannotateJob.status == "PROCESSING",
            YoloPreannotateJob.lease_owner == lease_owner,
            YoloPreannotateJob.lease_fencing_token == int(fencing_token),
        )
        .update(
            {
                YoloPreannotateJob.lease_expires_at: now
                + timedelta(seconds=max(int(lease_seconds), 30)),
                YoloPreannotateJob.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return bool(matched)


def _fenced_query(db: Session, job_id: int, lease_owner: str, fencing_token: int):
    return db.query(YoloPreannotateJob).filter(
        YoloPreannotateJob.id == int(job_id),
        YoloPreannotateJob.status == "PROCESSING",
        YoloPreannotateJob.lease_owner == lease_owner,
        YoloPreannotateJob.lease_fencing_token == int(fencing_token),
    )


def increment_progress_fenced(
    db: Session,
    *,
    job_id: int,
    lease_owner: str,
    fencing_token: int,
    success: bool,
    annotated: bool = True,
    lease_seconds: int = 900,
) -> bool:
    now = datetime.now()
    query = _fenced_query(db, job_id, lease_owner, fencing_token)
    values = {
        YoloPreannotateJob.processed_count: YoloPreannotateJob.processed_count + 1,
        YoloPreannotateJob.lease_expires_at: now + timedelta(seconds=max(int(lease_seconds), 30)),
        YoloPreannotateJob.updated_at: now,
    }
    if success and annotated:
        values[YoloPreannotateJob.success_count] = YoloPreannotateJob.success_count + 1
    elif not success:
        values[YoloPreannotateJob.failed_count] = YoloPreannotateJob.failed_count + 1
    matched = query.update(values, synchronize_session=False)
    db.flush()
    return bool(matched)


def finish_fenced(
    db: Session,
    *,
    job_id: int,
    lease_owner: str,
    fencing_token: int,
    status: str,
    error_message: str | None = None,
) -> bool:
    values = {
        YoloPreannotateJob.status: status,
        YoloPreannotateJob.error_message: error_message,
        YoloPreannotateJob.lease_owner: None,
        YoloPreannotateJob.lease_expires_at: None,
        YoloPreannotateJob.updated_at: datetime.now(),
    }
    matched = _fenced_query(db, job_id, lease_owner, fencing_token).update(
        values, synchronize_session=False
    )
    db.flush()
    return bool(matched)


def list_by_task_id(
    db: Session,
    task_id: int,
    *,
    search: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[YoloPreannotateJob], int]:
    q = db.query(YoloPreannotateJob).filter(YoloPreannotateJob.task_id == task_id)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        q = q.filter(
            or_(
                cast(YoloPreannotateJob.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloPreannotateJob.task_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloPreannotateJob.status.ilike(pattern, escape=LIKE_ESCAPE),
                YoloPreannotateJob.model_kind.ilike(pattern, escape=LIKE_ESCAPE),
                cast(YoloPreannotateJob.model_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloPreannotateJob.error_message.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloPreannotateJob.payload_json.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = q.count()
    rows = (
        q.order_by(YoloPreannotateJob.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def increment_progress(
    db: Session,
    job: YoloPreannotateJob,
    success: bool,
    *,
    annotated: bool = True,
) -> YoloPreannotateJob:
    job.processed_count = (job.processed_count or 0) + 1
    if success and annotated:
        job.success_count = (job.success_count or 0) + 1
    elif not success:
        job.failed_count = (job.failed_count or 0) + 1
    db.flush()
    db.refresh(job)
    return job
