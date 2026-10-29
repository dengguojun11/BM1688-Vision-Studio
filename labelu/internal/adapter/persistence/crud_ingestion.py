"""Persistence helpers for the creator/task-scoped ingestion read model."""

from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.ingestion import IngestionJob, MediaAsset


def get_job(db: Session, job_id: int) -> Optional[IngestionJob]:
    return (
        db.query(IngestionJob)
        .filter(IngestionJob.id == int(job_id))
        .first()
    )


def get_job_by_idempotency(
    db: Session, creator_id: int, idempotency_key: str
) -> Optional[IngestionJob]:
    return (
        db.query(IngestionJob)
        .filter(
            IngestionJob.created_by == int(creator_id),
            IngestionJob.idempotency_key == str(idempotency_key),
        )
        .first()
    )


def create_job(db: Session, job: IngestionJob) -> IngestionJob:
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def list_jobs(
    db: Session,
    *,
    user_id: int,
    is_superuser: bool,
    status: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> Tuple[list[IngestionJob], int]:
    query = db.query(IngestionJob)
    if not is_superuser:
        query = query.filter(IngestionJob.created_by == int(user_id))
    if status:
        query = query.filter(IngestionJob.status == str(status).upper())
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(IngestionJob.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(IngestionJob.source_connection_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                IngestionJob.source_type.ilike(pattern, escape=LIKE_ESCAPE),
                IngestionJob.status.ilike(pattern, escape=LIKE_ESCAPE),
                IngestionJob.manifest_sha256.ilike(pattern, escape=LIKE_ESCAPE),
                IngestionJob.error_code.ilike(pattern, escape=LIKE_ESCAPE),
                IngestionJob.error_message.ilike(pattern, escape=LIKE_ESCAPE),
                IngestionJob.source_snapshot_json.ilike(pattern, escape=LIKE_ESCAPE),
                IngestionJob.selection_json.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return rows, total


def get_asset(db: Session, asset_id: int) -> Optional[MediaAsset]:
    return (
        db.query(MediaAsset)
        .filter(MediaAsset.id == int(asset_id), MediaAsset.deleted_at.is_(None))
        .first()
    )


def create_asset(db: Session, asset: MediaAsset) -> MediaAsset:
    db.add(asset)
    db.flush()
    db.refresh(asset)
    return asset


def get_asset_by_identity(
    db: Session, creator_id: int, sha256: str, media_type: str
) -> Optional[MediaAsset]:
    return (
        db.query(MediaAsset)
        .filter(
            MediaAsset.created_by == int(creator_id),
            MediaAsset.sha256 == str(sha256).lower(),
            MediaAsset.media_type == str(media_type).upper(),
            MediaAsset.deleted_at.is_(None),
        )
        .first()
    )


def list_assets(
    db: Session,
    *,
    user_id: int,
    is_superuser: bool,
    ingestion_job_id: int | None = None,
    sha256: str | None = None,
    scan_status: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> Tuple[list[MediaAsset], int]:
    query = db.query(MediaAsset)
    query = query.filter(MediaAsset.deleted_at.is_(None))
    if not is_superuser:
        query = query.filter(MediaAsset.created_by == int(user_id))
    if ingestion_job_id is not None:
        query = query.filter(MediaAsset.ingestion_job_id == int(ingestion_job_id))
    if sha256:
        query = query.filter(MediaAsset.sha256 == str(sha256).strip().lower())
    if scan_status:
        query = query.filter(MediaAsset.scan_status == str(scan_status).strip().upper())
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(MediaAsset.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.object_uri.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.source_object_key.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.media_type.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.content_type.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.sha256.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.scan_status.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.quality_status.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.lifecycle_status.ilike(pattern, escape=LIKE_ESCAPE),
                MediaAsset.metadata_json.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return rows, total
