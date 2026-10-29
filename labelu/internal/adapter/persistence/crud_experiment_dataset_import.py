from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.experiment_dataset_import import ExperimentDatasetImport


def create(db: Session, row: ExperimentDatasetImport) -> ExperimentDatasetImport:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(
    db: Session, import_job_id: int, *, lock: bool = False
) -> ExperimentDatasetImport | None:
    query = db.query(ExperimentDatasetImport).filter(
        ExperimentDatasetImport.id == import_job_id,
        ExperimentDatasetImport.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def get_by_package_id(
    db: Session,
    package_id: str,
    *,
    lock: bool = False,
    include_deleted: bool = False,
) -> ExperimentDatasetImport | None:
    filters = [ExperimentDatasetImport.package_id == package_id]
    if not include_deleted:
        filters.append(ExperimentDatasetImport.deleted_at.is_(None))
    query = db.query(ExperimentDatasetImport).filter(*filters)
    if lock:
        query = query.with_for_update()
    return query.first()


def list_by(
    db: Session,
    *,
    package_id: str | None = None,
    status: str | None = None,
    model_id: str | None = None,
    lock: bool = False,
) -> list[ExperimentDatasetImport]:
    query = db.query(ExperimentDatasetImport).filter(
        ExperimentDatasetImport.deleted_at.is_(None),
    )
    if package_id:
        query = query.filter(ExperimentDatasetImport.package_id == package_id)
    if status:
        query = query.filter(ExperimentDatasetImport.status == status)
    if model_id:
        query = query.filter(ExperimentDatasetImport.model_id == model_id)
    if lock:
        query = query.with_for_update()
    return query.order_by(ExperimentDatasetImport.id.desc()).all()


def list_by_page(
    db: Session,
    *,
    package_id: str | None = None,
    status: str | None = None,
    model_id: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[ExperimentDatasetImport], int]:
    """Return a filtered page and the matching row count.

    ``list_by`` is intentionally kept as the unbounded legacy helper because
    the internal strategy callback still consumes the complete import history.
    Public evolution screens use this bounded variant so a large import table
    does not have to be loaded into the API process or browser.
    """
    query = db.query(ExperimentDatasetImport).filter(
        ExperimentDatasetImport.deleted_at.is_(None),
    )
    if package_id:
        query = query.filter(ExperimentDatasetImport.package_id == package_id)
    if status:
        query = query.filter(ExperimentDatasetImport.status == status)
    if model_id:
        query = query.filter(ExperimentDatasetImport.model_id == model_id)

    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(ExperimentDatasetImport.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.package_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.source_system.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.source_external_task_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.source_external_package_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.dataset_type.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.dataset_date.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.event_type.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.model_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.aggregation_key.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ExperimentDatasetImport.status.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )

    total = query.count()
    normalized_page = max(0, int(page))
    normalized_size = max(1, int(size))
    rows = (
        query.order_by(ExperimentDatasetImport.id.desc())
        .offset(normalized_page * normalized_size)
        .limit(normalized_size)
        .all()
    )
    return rows, total


def list_by_aggregation_key(
    db: Session, *, aggregation_key: str, lock: bool = False
) -> list[ExperimentDatasetImport]:
    query = db.query(ExperimentDatasetImport).filter(
        ExperimentDatasetImport.aggregation_key == aggregation_key,
        ExperimentDatasetImport.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    return query.order_by(ExperimentDatasetImport.id.asc()).all()


def update(
    db: Session, row: ExperimentDatasetImport, obj_in: dict[str, Any]
) -> ExperimentDatasetImport:
    for key, value in obj_in.items():
        setattr(row, key, value)
    db.flush()
    db.refresh(row)
    return row


def update_by_id(
    db: Session,
    *,
    import_job_id: int,
    obj_in: dict[str, Any],
) -> ExperimentDatasetImport | None:
    updates = {key: value for key, value in obj_in.items()}
    matched = (
        db.query(ExperimentDatasetImport)
        .filter(
            ExperimentDatasetImport.id == import_job_id,
            ExperimentDatasetImport.deleted_at.is_(None),
        )
        .update(updates, synchronize_session=False)
    )
    db.flush()
    if not matched:
        return None
    db.expire_all()
    return get(db=db, import_job_id=import_job_id)


def update_by_worker_lease(
    db: Session,
    *,
    import_job_id: int,
    owner: str,
    fencing_token: int,
    obj_in: dict[str, Any],
) -> bool:
    """Apply a state write only from the currently fenced Worker generation."""
    matched = (
        db.query(ExperimentDatasetImport)
        .filter(
            ExperimentDatasetImport.id == int(import_job_id),
            ExperimentDatasetImport.worker_lease_owner == owner,
            ExperimentDatasetImport.worker_fencing_token == int(fencing_token),
            ExperimentDatasetImport.worker_lease_expires_at.is_not(None),
            ExperimentDatasetImport.worker_lease_expires_at > datetime.now(),
        )
        .update(dict(obj_in), synchronize_session=False)
    )
    db.flush()
    return bool(matched)


def worker_lease_is_valid(
    db: Session,
    *,
    import_job_id: int,
    owner: str,
    fencing_token: int,
    now: datetime | None = None,
) -> bool:
    """Read the current, unexpired fencing generation from the database."""
    current = now or datetime.now()
    with db.no_autoflush:
        return (
            db.query(ExperimentDatasetImport.id)
            .filter(
                ExperimentDatasetImport.id == int(import_job_id),
                ExperimentDatasetImport.worker_lease_owner == str(owner),
                ExperimentDatasetImport.worker_fencing_token == int(fencing_token),
                ExperimentDatasetImport.worker_lease_expires_at.is_not(None),
                ExperimentDatasetImport.worker_lease_expires_at > current,
            )
            .first()
            is not None
        )


def claim_pending(
    db: Session,
    *,
    owner: str,
    lease_seconds: int = 300,
    limit: int = 1,
    now: datetime | None = None,
) -> list[tuple[int, int]]:
    """Atomically claim pending/stale imports for a dedicated Worker.

    Returns ``(job_id, fencing_token)`` pairs. The update predicate is repeated
    at write time so two Worker processes cannot both win the same job.
    """
    current = now or datetime.now()
    expires = current + timedelta(seconds=max(1, int(lease_seconds)))
    candidates = (
        db.query(ExperimentDatasetImport.id)
        .filter(
            ExperimentDatasetImport.deleted_at.is_(None),
            ExperimentDatasetImport.status.in_(["PENDING", "PROCESSING"]),
            or_(
                ExperimentDatasetImport.worker_lease_owner.is_(None),
                ExperimentDatasetImport.worker_lease_expires_at.is_(None),
                ExperimentDatasetImport.worker_lease_expires_at <= current,
            ),
        )
        # A waiting package is moved back to PENDING after the retry cooldown.
        # Ordering by id alone lets an old repeatedly-waiting row monopolize a
        # batch of one and starve newly accepted packages. Prefer rows that
        # have never been claimed, then fall back to the oldest update and id
        # for stable FIFO behavior among the same retry generation.
        .order_by(
            ExperimentDatasetImport.attempt_count.asc(),
            ExperimentDatasetImport.updated_at.asc(),
            ExperimentDatasetImport.id.asc(),
        )
        .limit(max(1, int(limit)))
        .all()
    )
    claimed: list[tuple[int, int]] = []
    for (job_id,) in candidates:
        updated = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.id == int(job_id),
                ExperimentDatasetImport.deleted_at.is_(None),
                ExperimentDatasetImport.status.in_(["PENDING", "PROCESSING"]),
                or_(
                    ExperimentDatasetImport.worker_lease_owner.is_(None),
                    ExperimentDatasetImport.worker_lease_expires_at.is_(None),
                    ExperimentDatasetImport.worker_lease_expires_at <= current,
                ),
            )
            .update(
                {
                    ExperimentDatasetImport.worker_lease_owner: owner,
                    ExperimentDatasetImport.worker_lease_expires_at: expires,
                    ExperimentDatasetImport.worker_fencing_token: ExperimentDatasetImport.worker_fencing_token + 1,
                    ExperimentDatasetImport.attempt_count: ExperimentDatasetImport.attempt_count + 1,
                    ExperimentDatasetImport.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if not updated:
            continue
        row = get(db, import_job_id=int(job_id))
        if row:
            claimed.append((int(row.id), int(row.worker_fencing_token or 0)))
    db.flush()
    return claimed


def requeue_waiting(
    db: Session,
    *,
    limit: int = 100,
    min_age_seconds: int = 0,
    now: datetime | None = None,
    status: str | None = None,
    model_id: str | None = None,
) -> list[int]:
    """Move eligible waiting imports back to ``PENDING``.

    Waiting rows are deliberately outside :func:`claim_pending` so that a
    package which is waiting for more samples, a model, or a category does not
    spin on every worker tick.  The dedicated Worker naturally scans only
    ``WAITING_FOR_SAMPLES``; model/category rows are requeued by the explicit
    prerequisite-change wake-up path. Callers may pass ``status`` and
    ``model_id`` for a targeted immediate wake-up.
    Active leases are never overwritten, so a slow Worker generation remains
    fenced even when a wake-up runs concurrently.
    """
    current = now or datetime.now()
    cutoff = current - timedelta(seconds=max(0, int(min_age_seconds)))
    statuses = (
        (str(status),)
        if status
        else ("WAITING_FOR_SAMPLES", "WAITING_FOR_MODEL", "WAITING_FOR_CATEGORY")
    )
    query = db.query(ExperimentDatasetImport.id).filter(
        ExperimentDatasetImport.deleted_at.is_(None),
        ExperimentDatasetImport.status.in_(statuses),
        or_(
            ExperimentDatasetImport.updated_at.is_(None),
            ExperimentDatasetImport.updated_at <= cutoff,
        ),
        or_(
            ExperimentDatasetImport.worker_lease_owner.is_(None),
            ExperimentDatasetImport.worker_lease_expires_at.is_(None),
            ExperimentDatasetImport.worker_lease_expires_at <= current,
        ),
    )
    if model_id is not None:
        query = query.filter(ExperimentDatasetImport.model_id == str(model_id))
    candidate_ids = [
        int(import_job_id)
        for (import_job_id,) in query.order_by(ExperimentDatasetImport.id.asc())
        .limit(max(1, int(limit)))
        .all()
    ]
    requeued: list[int] = []
    for import_job_id in candidate_ids:
        matched = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.id == import_job_id,
                ExperimentDatasetImport.deleted_at.is_(None),
                ExperimentDatasetImport.status.in_(statuses),
                *(
                    [ExperimentDatasetImport.model_id == str(model_id)]
                    if model_id is not None
                    else []
                ),
                or_(
                    ExperimentDatasetImport.updated_at.is_(None),
                    ExperimentDatasetImport.updated_at <= cutoff,
                ),
                or_(
                    ExperimentDatasetImport.worker_lease_owner.is_(None),
                    ExperimentDatasetImport.worker_lease_expires_at.is_(None),
                    ExperimentDatasetImport.worker_lease_expires_at <= current,
                ),
            )
            .update(
                {
                    ExperimentDatasetImport.status: "PENDING",
                    ExperimentDatasetImport.last_error: None,
                    ExperimentDatasetImport.started_at: None,
                    ExperimentDatasetImport.finished_at: None,
                    ExperimentDatasetImport.worker_lease_owner: None,
                    ExperimentDatasetImport.worker_lease_expires_at: None,
                    ExperimentDatasetImport.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if matched:
            requeued.append(import_job_id)
    db.flush()
    return requeued


def release_worker_lease(
    db: Session,
    *,
    import_job_id: int,
    owner: str,
    fencing_token: int,
    expires_at: datetime | None = None,
) -> bool:
    """Release only the lease held by the current Worker generation."""
    updates = {
        ExperimentDatasetImport.worker_lease_owner: None,
        ExperimentDatasetImport.worker_lease_expires_at: expires_at or datetime.now(),
        ExperimentDatasetImport.updated_at: datetime.now(),
    }
    matched = (
        db.query(ExperimentDatasetImport)
        .filter(
            ExperimentDatasetImport.id == int(import_job_id),
            ExperimentDatasetImport.worker_lease_owner == owner,
            ExperimentDatasetImport.worker_fencing_token == int(fencing_token),
            or_(
                ExperimentDatasetImport.worker_lease_expires_at.is_(None),
                ExperimentDatasetImport.worker_lease_expires_at > datetime.now(),
            ),
        )
        .update(updates, synchronize_session=False)
    )
    db.flush()
    return bool(matched)


def renew_worker_lease(
    db: Session,
    *,
    import_job_id: int,
    owner: str,
    fencing_token: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Extend a lease only for the current owner/token generation."""
    current = now or datetime.now()
    matched = (
        db.query(ExperimentDatasetImport)
        .filter(
            ExperimentDatasetImport.id == int(import_job_id),
            ExperimentDatasetImport.worker_lease_owner == owner,
            ExperimentDatasetImport.worker_fencing_token == int(fencing_token),
            ExperimentDatasetImport.status.in_(["PENDING", "PROCESSING"]),
        )
        .update(
            {
                ExperimentDatasetImport.worker_lease_expires_at: current
                + timedelta(seconds=max(1, int(lease_seconds))),
                ExperimentDatasetImport.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return bool(matched)


def soft_delete(db: Session, row: ExperimentDatasetImport) -> ExperimentDatasetImport:
    row.deleted_at = datetime.now()
    db.flush()
    db.refresh(row)
    return row
