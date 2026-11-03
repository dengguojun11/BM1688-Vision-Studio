from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from labelu.internal.domain.models.auto_label_job import AutoLabelJob, AutoLabelStatus

AUTO_SAMPLE_PREANNOTATE_JOB_KIND = "AUTO_SAMPLE_PREANNOTATE"


def create(
    db: Session,
    task_id: int,
    user_id: int,
    sample_count: int,
    filter_by_labels: bool,
    *,
    job_kind: str = "MANUAL_BATCH",
    source_hint: str | None = None,
    source_key: str | None = None,
    payload_json: str | None = None,
) -> AutoLabelJob:
    job = AutoLabelJob(
        task_id=task_id,
        created_by=user_id,
        sample_count=sample_count,
        filter_by_labels=filter_by_labels,
        job_kind=job_kind,
        source_hint=source_hint,
        source_key=source_key,
        payload_json=payload_json,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[AutoLabelJob]:
    return db.query(AutoLabelJob).filter(AutoLabelJob.id == job_id).first()


def get_by_source_key(db: Session, source_key: str) -> Optional[AutoLabelJob]:
    return db.query(AutoLabelJob).filter(AutoLabelJob.source_key == source_key).first()


def list_runnable_auto_sample_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[AutoLabelJob]:
    now = now or datetime.now()
    return (
        db.query(AutoLabelJob)
        .filter(
            AutoLabelJob.job_kind == AUTO_SAMPLE_PREANNOTATE_JOB_KIND,
            or_(
                AutoLabelJob.status == AutoLabelStatus.PENDING.value,
                and_(
                    AutoLabelJob.status == AutoLabelStatus.PROCESSING.value,
                    or_(
                        AutoLabelJob.lease_expires_at.is_(None),
                        AutoLabelJob.lease_expires_at < now,
                    ),
                ),
            ),
        )
        .order_by(AutoLabelJob.id.asc())
        .limit(max(int(limit), 1))
        .all()
    )


def claim_auto_sample_job(
    db: Session,
    *,
    job_id: int,
    lease_owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    return claim_auto_sample_job_with_token(
        db,
        job_id=job_id,
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
        now=now,
    ) is not None


def claim_auto_sample_job_with_token(
    db: Session,
    *,
    job_id: int,
    lease_owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> int | None:
    """Atomically claim a job and return its new fencing token.

    The token is incremented in the same UPDATE as the lease claim.  This
    makes takeover safe on MySQL and SQLite: a paused old worker retains its
    old token and every subsequent write can be rejected by the token guard.
    """
    now = now or datetime.now()
    lease_expires_at = now + timedelta(seconds=max(int(lease_seconds), 30))
    matched = (
        db.query(AutoLabelJob)
        .filter(
            AutoLabelJob.id == job_id,
            AutoLabelJob.job_kind == AUTO_SAMPLE_PREANNOTATE_JOB_KIND,
            or_(
                AutoLabelJob.status == AutoLabelStatus.PENDING.value,
                and_(
                    AutoLabelJob.status == AutoLabelStatus.PROCESSING.value,
                    or_(
                        AutoLabelJob.lease_expires_at.is_(None),
                        AutoLabelJob.lease_expires_at < now,
                    ),
                ),
            ),
        )
        .update(
            {
                AutoLabelJob.status: AutoLabelStatus.PROCESSING.value,
                AutoLabelJob.lease_owner: lease_owner,
                AutoLabelJob.lease_expires_at: lease_expires_at,
                AutoLabelJob.attempt_count: AutoLabelJob.attempt_count + 1,
                AutoLabelJob.lease_fencing_token: AutoLabelJob.lease_fencing_token + 1,
                AutoLabelJob.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    if not matched:
        return None
    token = (
        db.query(AutoLabelJob.lease_fencing_token)
        .filter(AutoLabelJob.id == int(job_id), AutoLabelJob.lease_owner == lease_owner)
        .scalar()
    )
    return int(token or 0)


def get_active_by_task_id(db: Session, task_id: int) -> Optional[AutoLabelJob]:
    return (
        db.query(AutoLabelJob)
        .filter(
            AutoLabelJob.task_id == task_id,
            AutoLabelJob.status.in_(
                [AutoLabelStatus.PENDING.value, AutoLabelStatus.PROCESSING.value]
            ),
        )
        .order_by(AutoLabelJob.id.desc())
        .first()
    )


def cancel_pending_for_task(
    db: Session,
    task_id: int,
    *,
    message: str = "superseded by a newer task preannotation job",
) -> int:
    """Cancel queued jobs when a full-task job replaces them.

    A label/config update schedules a job containing every current sample.
    Leaving older queued jobs for the same task makes the UI show a stale
    0/N job while the worker is still working through redundant work.
    PROCESSING jobs are deliberately left alone; their lease/fencing handles
    takeover safety.
    """
    rows = (
        db.query(AutoLabelJob)
        .filter(
            AutoLabelJob.task_id == int(task_id),
            AutoLabelJob.job_kind == AUTO_SAMPLE_PREANNOTATE_JOB_KIND,
            AutoLabelJob.status == AutoLabelStatus.PENDING.value,
        )
        .all()
    )
    now = datetime.now()
    for job in rows:
        job.status = AutoLabelStatus.CANCELLED.value
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_message = message[:4000]
        job.updated_at = now
    db.flush()
    return len(rows)


def cancel_superseded_pending_jobs(db: Session) -> int:
    """Collapse stale queued full-task jobs into the newest full-task job."""
    rows = (
        db.query(AutoLabelJob)
        .filter(
            AutoLabelJob.job_kind == AUTO_SAMPLE_PREANNOTATE_JOB_KIND,
            AutoLabelJob.status == AutoLabelStatus.PENDING.value,
        )
        .order_by(AutoLabelJob.task_id.asc(), AutoLabelJob.id.asc())
        .all()
    )
    by_task: dict[int, list[AutoLabelJob]] = {}
    for row in rows:
        by_task.setdefault(int(row.task_id), []).append(row)
    cancelled = 0
    now = datetime.now()
    for task_rows in by_task.values():
        full_rows = [
            row
            for row in task_rows
            if str(row.source_hint or "") in {"task_config_update", "labels_apply"}
        ]
        if not full_rows:
            continue
        latest_full = max(full_rows, key=lambda row: int(row.id))
        for row in task_rows:
            if int(row.id) >= int(latest_full.id):
                continue
            row.status = AutoLabelStatus.CANCELLED.value
            row.lease_owner = None
            row.lease_expires_at = None
            row.error_message = (
                f"superseded by full-task job {int(latest_full.id)}"
            )
            row.updated_at = now
            cancelled += 1
    db.flush()
    return cancelled


def update_status(db: Session, job: AutoLabelJob, status: str, **kwargs) -> AutoLabelJob:
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    db.flush()
    db.refresh(job)
    return job


def increment_progress(db: Session, job: AutoLabelJob, success: bool) -> AutoLabelJob:
    job.processed_count = (job.processed_count or 0) + 1
    if success:
        job.success_count = (job.success_count or 0) + 1
    else:
        job.failed_count = (job.failed_count or 0) + 1
    db.flush()
    db.refresh(job)
    return job
