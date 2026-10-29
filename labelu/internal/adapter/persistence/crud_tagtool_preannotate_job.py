from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import String, and_, cast, or_

from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.tagtool_preannotate_job import TagtoolPreannotateJob


def create(
    db: Session,
    *,
    task_id: int,
    user_id: int,
    sample_count: int,
    model_kind: str,
    model_id: int,
    payload_json: str | None = None,
) -> TagtoolPreannotateJob:
    job = TagtoolPreannotateJob(
        task_id=task_id,
        created_by=user_id,
        sample_count=sample_count,
        model_kind=model_kind,
        model_id=model_id,
        payload_json=payload_json,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[TagtoolPreannotateJob]:
    return db.query(TagtoolPreannotateJob).filter(TagtoolPreannotateJob.id == job_id).first()


def get_active_by_task_id(db: Session, task_id: int) -> Optional[TagtoolPreannotateJob]:
    return (
        db.query(TagtoolPreannotateJob)
        .filter(
            TagtoolPreannotateJob.task_id == task_id,
            TagtoolPreannotateJob.status.in_(["PENDING", "PROCESSING"]),
        )
        .order_by(TagtoolPreannotateJob.id.desc())
        .first()
    )


def list_runnable(db: Session, *, now: datetime | None = None, limit: int = 1):
    now = now or datetime.now()
    return (
        db.query(TagtoolPreannotateJob)
        .filter(
            or_(
                TagtoolPreannotateJob.status == "PENDING",
                and_(
                    TagtoolPreannotateJob.status == "PROCESSING",
                    or_(
                        TagtoolPreannotateJob.lease_expires_at.is_(None),
                        TagtoolPreannotateJob.lease_expires_at < now,
                    ),
                ),
            )
        )
        .order_by(TagtoolPreannotateJob.id.asc())
        .limit(max(int(limit), 1))
        .all()
    )


def claim_with_token(
    db: Session, *, job_id: int, lease_owner: str, lease_seconds: int, now: datetime | None = None
) -> int | None:
    now = now or datetime.now()
    expires = now + timedelta(seconds=max(int(lease_seconds), 30))
    matched = (
        db.query(TagtoolPreannotateJob)
        .filter(
            TagtoolPreannotateJob.id == int(job_id),
            or_(
                TagtoolPreannotateJob.status == "PENDING",
                and_(
                    TagtoolPreannotateJob.status == "PROCESSING",
                    or_(
                        TagtoolPreannotateJob.lease_expires_at.is_(None),
                        TagtoolPreannotateJob.lease_expires_at < now,
                    ),
                ),
            ),
        )
        .update(
            {
                TagtoolPreannotateJob.status: "PROCESSING",
                TagtoolPreannotateJob.lease_owner: lease_owner,
                TagtoolPreannotateJob.lease_expires_at: expires,
                TagtoolPreannotateJob.attempt_count: TagtoolPreannotateJob.attempt_count + 1,
                TagtoolPreannotateJob.lease_fencing_token: TagtoolPreannotateJob.lease_fencing_token + 1,
                TagtoolPreannotateJob.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    if not matched:
        return None
    return int(
        db.query(TagtoolPreannotateJob.lease_fencing_token)
        .filter(TagtoolPreannotateJob.id == int(job_id), TagtoolPreannotateJob.lease_owner == lease_owner)
        .scalar()
        or 0
    )


def increment_progress_fenced(
    db: Session, *, job_id: int, lease_owner: str, lease_fencing_token: int, success: bool
) -> bool:
    values = {
        TagtoolPreannotateJob.processed_count: TagtoolPreannotateJob.processed_count + 1,
        TagtoolPreannotateJob.updated_at: datetime.now(),
    }
    field = TagtoolPreannotateJob.success_count if success else TagtoolPreannotateJob.failed_count
    values[field] = field + 1
    changed = (
        db.query(TagtoolPreannotateJob)
        .filter(
            TagtoolPreannotateJob.id == int(job_id),
            TagtoolPreannotateJob.status == "PROCESSING",
            TagtoolPreannotateJob.lease_owner == lease_owner,
            TagtoolPreannotateJob.lease_fencing_token == int(lease_fencing_token),
        )
        .update(values, synchronize_session=False)
    )
    db.flush()
    return bool(changed)


def renew_fenced(
    db: Session, *, job_id: int, lease_owner: str, lease_fencing_token: int,
    lease_seconds: int, now: datetime | None = None,
) -> bool:
    now = now or datetime.now()
    changed = db.query(TagtoolPreannotateJob).filter(
        TagtoolPreannotateJob.id == int(job_id),
        TagtoolPreannotateJob.status == "PROCESSING",
        TagtoolPreannotateJob.lease_owner == lease_owner,
        TagtoolPreannotateJob.lease_fencing_token == int(lease_fencing_token),
    ).update({
        TagtoolPreannotateJob.lease_expires_at: now + timedelta(seconds=max(int(lease_seconds), 30)),
        TagtoolPreannotateJob.updated_at: now,
    }, synchronize_session=False)
    db.flush()
    return bool(changed)


def finish_fenced(
    db: Session, *, job_id: int, lease_owner: str, lease_fencing_token: int, status: str, error_message: str | None = None
) -> bool:
    changed = (
        db.query(TagtoolPreannotateJob)
        .filter(
            TagtoolPreannotateJob.id == int(job_id),
            TagtoolPreannotateJob.status == "PROCESSING",
            TagtoolPreannotateJob.lease_owner == lease_owner,
            TagtoolPreannotateJob.lease_fencing_token == int(lease_fencing_token),
        )
        .update(
            {
                TagtoolPreannotateJob.status: status,
                TagtoolPreannotateJob.error_message: error_message,
                TagtoolPreannotateJob.lease_owner: None,
                TagtoolPreannotateJob.lease_expires_at: None,
                TagtoolPreannotateJob.updated_at: datetime.now(),
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return bool(changed)


def update_status(
    db: Session, job: TagtoolPreannotateJob, status: str, **kwargs
) -> TagtoolPreannotateJob:
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    db.flush()
    db.refresh(job)
    return job


def list_by_task_id(
    db: Session,
    task_id: int,
    *,
    search: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[TagtoolPreannotateJob], int]:
    q = db.query(TagtoolPreannotateJob).filter(TagtoolPreannotateJob.task_id == task_id)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        q = q.filter(
            or_(
                cast(TagtoolPreannotateJob.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(TagtoolPreannotateJob.task_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TagtoolPreannotateJob.status.ilike(pattern, escape=LIKE_ESCAPE),
                TagtoolPreannotateJob.model_kind.ilike(pattern, escape=LIKE_ESCAPE),
                cast(TagtoolPreannotateJob.model_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TagtoolPreannotateJob.error_message.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TagtoolPreannotateJob.payload_json.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = q.count()
    rows = (
        q.order_by(TagtoolPreannotateJob.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def increment_progress(
    db: Session, job: TagtoolPreannotateJob, success: bool
) -> TagtoolPreannotateJob:
    job.processed_count = (job.processed_count or 0) + 1
    if success:
        job.success_count = (job.success_count or 0) + 1
    else:
        job.failed_count = (job.failed_count or 0) + 1
    db.flush()
    db.refresh(job)
    return job
