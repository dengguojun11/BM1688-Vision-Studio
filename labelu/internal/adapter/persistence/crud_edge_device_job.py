from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.edge_device_job import EdgeDeviceJob


def create(
    db: Session,
    *,
    export_job_id: int,
    task_id: int,
    user_id: int,
    payload: str,
) -> EdgeDeviceJob:
    row = EdgeDeviceJob(
        export_job_id=export_job_id,
        task_id=task_id,
        created_by=user_id,
        payload=payload,
        status="PENDING",
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, job_id: int) -> Optional[EdgeDeviceJob]:
    return db.query(EdgeDeviceJob).filter(EdgeDeviceJob.id == job_id).first()


def claim_next(db: Session, *, device_id: str) -> Optional[EdgeDeviceJob]:
    # FOR UPDATE is ignored by SQLite, so selecting a row and then mutating it
    # is not enough to prevent two workers from receiving the same job. The
    # conditional UPDATE is atomic on both SQLite and MySQL; only the worker
    # that changes PENDING to RUNNING owns the job.
    attempted_ids: set[int] = set()
    while True:
        candidates = (
            db.query(EdgeDeviceJob.id)
            .filter(
                EdgeDeviceJob.status == "PENDING",
                ~EdgeDeviceJob.id.in_(attempted_ids) if attempted_ids else True,
            )
            .order_by(EdgeDeviceJob.id.asc())
            .limit(32)
            .all()
        )
        if not candidates:
            return None
        for (job_id,) in candidates:
            attempted_ids.add(job_id)
            claimed_at = datetime.now()
            updated = (
                db.query(EdgeDeviceJob)
                .filter(
                    EdgeDeviceJob.id == job_id,
                    EdgeDeviceJob.status == "PENDING",
                )
                .update(
                    {
                        EdgeDeviceJob.status: "RUNNING",
                        EdgeDeviceJob.device_id: device_id,
                        EdgeDeviceJob.claimed_at: claimed_at,
                        EdgeDeviceJob.updated_at: claimed_at,
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                continue
            row = get(db, job_id)
            if row is not None:
                return row
        if len(attempted_ids) >= 32:
            return None


def finish(
    db: Session,
    *,
    row: EdgeDeviceJob,
    status: str,
    result: str,
    error_message: str | None,
) -> EdgeDeviceJob:
    row.status = status
    row.result = result
    row.error_message = error_message
    row.completed_at = datetime.now()
    db.flush()
    db.refresh(row)
    return row


def finish_if_claimed(
    db: Session,
    *,
    job_id: int,
    device_id: str,
    status: str,
    result: str,
    error_message: str | None,
) -> Optional[EdgeDeviceJob]:
    """Finish a job only if this device still owns its RUNNING lease."""
    completed_at = datetime.now()
    updated = (
        db.query(EdgeDeviceJob)
        .filter(
            EdgeDeviceJob.id == job_id,
            EdgeDeviceJob.status == "RUNNING",
            EdgeDeviceJob.device_id == device_id,
        )
        .update(
            {
                EdgeDeviceJob.status: status,
                EdgeDeviceJob.result: result,
                EdgeDeviceJob.error_message: error_message,
                EdgeDeviceJob.completed_at: completed_at,
                EdgeDeviceJob.updated_at: completed_at,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        return None
    return get(db, job_id)
