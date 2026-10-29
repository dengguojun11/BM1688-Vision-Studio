from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import String, cast, or_, update
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.provider_job import PredictionSet, ProviderJob
from labelu.internal.domain.models.prediction_review import PredictionSetProjection, PredictionSetReviewAudit
from labelu.internal.common.task_access import is_task_superuser


def get_job(db: Session, job_id: int) -> ProviderJob | None:
    return db.query(ProviderJob).filter(ProviderJob.id == int(job_id)).first()


def get_job_by_signature(db: Session, creator_id: int, signature: str) -> ProviderJob | None:
    return db.query(ProviderJob).filter(
        ProviderJob.created_by == int(creator_id), ProviderJob.request_signature == signature
    ).first()


def create_job(db: Session, row: ProviderJob) -> ProviderJob:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def claim_next_job(db: Session, *, worker_id: str, lease_seconds: int = 300) -> ProviderJob | None:
    """Atomically claim one queued or expired provider job."""
    now = datetime.now()
    candidate = db.query(ProviderJob).filter(
        ProviderJob.status == "QUEUED",
        or_(ProviderJob.lease_expires_at.is_(None), ProviderJob.lease_expires_at < now),
    ).order_by(ProviderJob.created_at.asc(), ProviderJob.id.asc()).first()
    if candidate is None:
        return None
    changed = db.query(ProviderJob).filter(
        ProviderJob.id == candidate.id,
        ProviderJob.status == "QUEUED",
        or_(ProviderJob.lease_expires_at.is_(None), ProviderJob.lease_expires_at < now),
    ).update({
        ProviderJob.status: "RUNNING", ProviderJob.lease_owner: str(worker_id),
        ProviderJob.lease_expires_at: now + timedelta(seconds=max(30, int(lease_seconds))),
        ProviderJob.started_at: candidate.started_at or now,
        ProviderJob.attempt_count: ProviderJob.attempt_count + 1,
    }, synchronize_session=False)
    if not changed:
        return None
    db.flush()
    return get_job(db, candidate.id)


def list_jobs(
    db: Session,
    *,
    user_id: int,
    is_superuser: bool,
    status: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
):
    query = db.query(ProviderJob)
    if not is_superuser:
        query = query.filter(ProviderJob.created_by == int(user_id))
    if status:
        query = query.filter(ProviderJob.status == status)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(ProviderJob.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ProviderJob.provider_key.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.provider_version.ilike(pattern, escape=LIKE_ESCAPE),
                cast(ProviderJob.model_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ProviderJob.contract_version.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.request_signature.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.status.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.error_code.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.error_message.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.input_asset_ids_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    total = query.count()
    rows = (
        query.order_by(ProviderJob.created_at.desc(), ProviderJob.id.desc())
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, int(size)))
        .all()
    )
    return rows, total


def get_prediction_set(db: Session, set_id: int) -> PredictionSet | None:
    return db.query(PredictionSet).filter(PredictionSet.id == int(set_id)).first()


def get_prediction_by_job_version(db: Session, job_id: int, version: int) -> PredictionSet | None:
    return db.query(PredictionSet).filter(
        PredictionSet.provider_job_id == int(job_id), PredictionSet.version == int(version)
    ).first()


def create_prediction_set(db: Session, row: PredictionSet) -> PredictionSet:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_review_audit_by_key(db: Session, prediction_set_id: int, idempotency_key: str) -> PredictionSetReviewAudit | None:
    return db.query(PredictionSetReviewAudit).filter(
        PredictionSetReviewAudit.prediction_set_id == int(prediction_set_id),
        PredictionSetReviewAudit.idempotency_key == idempotency_key,
    ).first()


def create_review_audit(db: Session, row: PredictionSetReviewAudit) -> PredictionSetReviewAudit:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def list_review_audits(db: Session, prediction_set_id: int) -> list[PredictionSetReviewAudit]:
    return db.query(PredictionSetReviewAudit).filter(
        PredictionSetReviewAudit.prediction_set_id == int(prediction_set_id)
    ).order_by(PredictionSetReviewAudit.created_at.asc(), PredictionSetReviewAudit.id.asc()).all()


def get_projection(db: Session, prediction_set_id: int) -> PredictionSetProjection | None:
    return db.query(PredictionSetProjection).filter(
        PredictionSetProjection.prediction_set_id == int(prediction_set_id)
    ).first()


def get_projection_by_key(db: Session, idempotency_key: str) -> PredictionSetProjection | None:
    return db.query(PredictionSetProjection).filter(
        PredictionSetProjection.idempotency_key == idempotency_key
    ).first()


def create_projection(db: Session, row: PredictionSetProjection) -> PredictionSetProjection:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def list_prediction_sets(db: Session, *, user_id: int, is_superuser: bool,
                         provider_job_id: int | None = None,
                         sample_id: int | None = None, review_status: str | None = None,
                         page: int = 0, size: int = 100,
                         search: str | None = None):
    # ProviderJob is joined only for searchable provider/request metadata. It
    # is a one-to-one parent, so the join cannot duplicate PredictionSet rows.
    query = db.query(PredictionSet).join(
        ProviderJob, ProviderJob.id == PredictionSet.provider_job_id
    )
    if not is_superuser:
        query = query.filter(PredictionSet.created_by == int(user_id))
    if provider_job_id is not None:
        query = query.filter(PredictionSet.provider_job_id == int(provider_job_id))
    if sample_id is not None:
        query = query.filter(PredictionSet.sample_id == int(sample_id))
    if review_status:
        query = query.filter(PredictionSet.review_status == review_status)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(PredictionSet.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(PredictionSet.provider_job_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(PredictionSet.sample_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(PredictionSet.version, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                PredictionSet.payload_uri.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.payload_sha256.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.result_manifest_uri.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                PredictionSet.result_sha256.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.status.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.review_status.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.source.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.error_code.ilike(pattern, escape=LIKE_ESCAPE),
                PredictionSet.error_message.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.provider_key.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.provider_version.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.request_signature.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderJob.status.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = query.order_by(PredictionSet.created_at.desc(), PredictionSet.id.desc()).offset(int(page) * int(size)).limit(int(size)).all()
    return rows, total
