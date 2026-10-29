"""Creator/task-scoped ingestion read model and compatibility registration service."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_attachment, crud_datasource, crud_ingestion, crud_media_upload, crud_task
from labelu.internal.application.command.ingestion import (
    CreateIngestionJobCommand,
    RegisterMediaAssetCommand,
    RegisterUploadedAssetCommand,
)
from labelu.internal.application.response.ingestion import (
    IngestionJobResponse,
    MediaAssetResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.ingestion import IngestionJob, MediaAsset
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def _error(message: str, http_status: int = status.HTTP_400_BAD_REQUEST) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=http_status,
        message=message,
    )


def _forbidden() -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_403_FORBIDDEN,
        message="无权访问该接入资源",
    )


def _not_found(message: str = "接入资源不存在") -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND,
        message=message,
    )


def _owner_access(owner_id: int | None, user: User) -> None:
    """Use creator ownership for the一期 ingestion boundary."""
    if is_task_superuser(user) or int(owner_id or 0) == int(user.id):
        return
    raise _forbidden()


def _task_access(db: Session, task_id: int, user: User) -> Task:
    query = crud_task.visible_query(
        db,
        owner_id=user.id,
        list_all_tasks=is_task_superuser(user),
    ).filter(Task.id == int(task_id))
    task = query.first()
    if task is None:
        raise _forbidden()
    return task


def _json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


_SENSITIVE_KEYS = {"password", "secret", "secret_access_key", "access_key", "token", "authorization"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _dump(value: dict[str, Any] | None) -> str:
    return json.dumps(_redact(value or {}), ensure_ascii=False, separators=(",", ":"))


def _job_response(row: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse(
        id=row.id,
        source_connection_id=row.source_connection_id,
        source_type=row.source_type,
        idempotency_key=row.idempotency_key,
        source_snapshot=_json_object(row.source_snapshot_json),
        selection=_json_object(row.selection_json),
        status=row.status,
        manifest_sha256=row.manifest_sha256,
        total_count=row.total_count or 0,
        accepted_count=row.accepted_count or 0,
        quarantined_count=row.quarantined_count or 0,
        failed_count=row.failed_count or 0,
        error_code=row.error_code,
        error_message=row.error_message,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _asset_response(row: MediaAsset) -> MediaAssetResponse:
    return MediaAssetResponse(
        id=row.id,
        ingestion_job_id=row.ingestion_job_id,
        object_uri=row.object_uri,
        asset_uri=row.object_uri,
        source_object_key=row.source_object_key,
        media_type=row.media_type,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        width=row.width,
        height=row.height,
        duration_ms=row.duration_ms,
        frame_index=row.frame_index,
        scan_status=row.scan_status,
        scan_provider=row.scan_provider,
        scan_signature=row.scan_signature,
        scan_error=row.scan_error,
        scanned_at=row.scanned_at,
        quality_status=row.quality_status,
        lifecycle_status=row.lifecycle_status,
        metadata=_json_object(row.metadata_json),
        attachment_id=row.attachment_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create_job(
    db: Session,
    cmd: CreateIngestionJobCommand,
    current_user: User,
    *,
    idempotency_key: str | None = None,
) -> IngestionJobResponse:
    key = (idempotency_key or cmd.idempotency_key or "").strip()
    if not key:
        raise _error("必须提供 Idempotency-Key", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if len(key) > 256:
        raise _error("Idempotency-Key 过长")
    if cmd.source_connection_id is not None:
        source = crud_datasource.get(db, cmd.source_connection_id)
        # DataSource retains historical scope columns, but the一期 boundary
        # authorizes a connection only by its creator ownership.
        if source is None:
            raise _not_found("数据源不存在")
        if not (is_task_superuser(current_user) or source.created_by == current_user.id):
            raise _forbidden()
        if getattr(source, "status", "ACTIVE") != "ACTIVE":
            raise _error("数据源已停用", status.HTTP_409_CONFLICT)

    existing = crud_ingestion.get_job_by_idempotency(db, current_user.id, key)
    if existing is not None:
        return _job_response(existing)

    row = IngestionJob(
        source_connection_id=cmd.source_connection_id,
        source_type=cmd.source_type,
        idempotency_key=key,
        source_snapshot_json=_dump(cmd.source_snapshot),
        selection_json=_dump(cmd.selection),
        status="QUEUED",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    try:
        with begin_transaction(db):
            crud_ingestion.create_job(db, row)
    except IntegrityError:
        # A concurrent retry may win the creator/key constraint.  Read
        # its immutable result instead of creating a second job.
        existing = crud_ingestion.get_job_by_idempotency(db, current_user.id, key)
        if existing is not None:
            return _job_response(existing)
        raise
    return _job_response(row)


async def get_job(db: Session, job_id: int, current_user: User) -> IngestionJobResponse:
    row = crud_ingestion.get_job(db, job_id)
    if row is None:
        raise _not_found()
    _owner_access(row.created_by, current_user)
    return _job_response(row)


async def cancel_job(
    db: Session, job_id: int, current_user: User
) -> IngestionJobResponse:
    """Cancel a queued or running ingestion job within its creator boundary."""
    row = crud_ingestion.get_job(db, job_id)
    if row is None:
        raise _not_found("接入作业不存在")
    _owner_access(row.created_by, current_user)
    if row.status not in {"QUEUED", "RUNNING"}:
        raise _error("当前接入作业状态不可取消", status.HTTP_409_CONFLICT)
    with begin_transaction(db):
        row.status = "CANCELLED"
        row.completed_at = datetime.now()
        row.updated_by = current_user.id
        db.flush()
        db.refresh(row)
    return _job_response(row)


async def list_jobs(
    db: Session,
    current_user: User,
    *,
    status_filter: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> tuple[list[IngestionJobResponse], int]:
    allowed = {"QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}
    normalized = str(status_filter).strip().upper() if status_filter else None
    if normalized and normalized not in allowed:
        raise _error("接入作业状态不受支持")
    rows, total = crud_ingestion.list_jobs(
        db,
        user_id=current_user.id,
        is_superuser=is_task_superuser(current_user),
        status=normalized,
        search=search,
        page=page,
        size=size,
    )
    return [_job_response(row) for row in rows], total


async def register_asset(
    db: Session,
    job_id: int,
    cmd: RegisterMediaAssetCommand,
    current_user: User,
) -> MediaAssetResponse:
    job = crud_ingestion.get_job(db, job_id)
    if job is None:
        raise _not_found("接入作业不存在")
    _owner_access(job.created_by, current_user)
    if cmd.ingestion_job_id is not None and int(cmd.ingestion_job_id) != int(job.id):
        raise _error("ingestion_job_id 与路径不一致")
    if job.status in {"FAILED", "CANCELLED"}:
        raise _error("当前接入作业不允许登记资产", status.HTTP_409_CONFLICT)
    duplicate = crud_ingestion.get_asset_by_identity(
        db, current_user.id, cmd.sha256, cmd.media_type
    )
    if duplicate is not None:
        return _asset_response(duplicate)
    row = MediaAsset(
        ingestion_job_id=job.id,
        object_uri=cmd.object_uri or cmd.asset_uri or "",
        source_object_key=cmd.source_object_key,
        media_type=cmd.media_type,
        content_type=cmd.content_type,
        size_bytes=cmd.size_bytes,
        sha256=cmd.sha256,
        width=cmd.width,
        height=cmd.height,
        duration_ms=cmd.duration_ms,
        frame_index=cmd.frame_index,
        scan_status=cmd.scan_status,
        scan_provider=cmd.scan_provider,
        scan_signature=cmd.scan_signature,
        scan_error=cmd.scan_error,
        scanned_at=datetime.now() if cmd.scan_status in {"PASSED", "FAILED", "QUARANTINED"} else None,
        quality_status=cmd.quality_status,
        lifecycle_status=cmd.lifecycle_status,
        metadata_json=_dump(cmd.metadata),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        crud_ingestion.create_asset(db, row)
        job.total_count = int(job.total_count or 0) + 1
        if row.scan_status == "PASSED":
            job.accepted_count = int(job.accepted_count or 0) + 1
        elif row.scan_status in {"QUARANTINED", "SCANNING", "PENDING"}:
            job.quarantined_count = int(job.quarantined_count or 0) + 1
        elif row.scan_status == "FAILED":
            job.failed_count = int(job.failed_count or 0) + 1
        if job.status == "QUEUED":
            job.status = "RUNNING"
        job.updated_by = current_user.id
        db.flush()
        db.refresh(job)
    return _asset_response(row)


async def register_uploaded_asset(
    db: Session,
    job_id: int,
    cmd: RegisterUploadedAssetCommand,
    current_user: User,
) -> MediaAssetResponse:
    """Bridge a completed legacy upload into the creator/task-scoped flow."""
    job = crud_ingestion.get_job(db, job_id)
    if job is None:
        raise _not_found("接入作业不存在")
    _owner_access(job.created_by, current_user)
    session = crud_media_upload.get_completed(db, cmd.upload_session_id.strip())
    if session is None:
        raise _error("上传会话尚未完成", status.HTTP_409_CONFLICT)
    if session.created_by != current_user.id and not is_task_superuser(current_user):
        raise _forbidden()
    attachment = crud_attachment.get(db, int(session.attachment_id or 0)) if session.attachment_id else None
    if attachment is None or attachment.deleted_at is not None:
        raise _not_found("上传附件不存在")
    _task_access(db, int(session.task_id), current_user)
    digest = (session.sha256 or attachment.sha256 or "").strip().lower()
    if len(digest) != 64:
        raise _error("上传附件缺少有效 SHA256", status.HTTP_422_UNPROCESSABLE_ENTITY)
    media_type = (attachment.content_type or "application/octet-stream").split("/", 1)[0].upper()
    media_type = {"IMAGE": "IMAGE", "VIDEO": "VIDEO", "AUDIO": "AUDIO"}.get(media_type, "FILE")
    duplicate = crud_ingestion.get_asset_by_identity(db, current_user.id, digest, media_type)
    if duplicate is not None:
        return _asset_response(duplicate)
    row = MediaAsset(
        ingestion_job_id=job.id,
        object_uri=attachment.url or attachment.path or "",
        source_object_key=attachment.path,
        media_type=media_type,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes or session.total_size,
        sha256=digest,
        scan_status=session.scan_status or "PENDING",
        scan_provider=session.scan_provider,
        scan_signature=session.scan_signature,
        scan_error=session.scan_error,
        scanned_at=session.scanned_at,
        quality_status="PENDING",
        lifecycle_status="ACTIVE",
        attachment_id=attachment.id,
        metadata_json=_dump({"filename": attachment.filename, "upload_session_id": session.session_id}),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        crud_ingestion.create_asset(db, row)
        job.total_count = int(job.total_count or 0) + 1
        if row.scan_status == "PASSED":
            job.accepted_count = int(job.accepted_count or 0) + 1
        elif row.scan_status in {"QUARANTINED", "SCANNING", "PENDING"}:
            job.quarantined_count = int(job.quarantined_count or 0) + 1
        elif row.scan_status == "FAILED":
            job.failed_count = int(job.failed_count or 0) + 1
        if job.status == "QUEUED":
            job.status = "RUNNING"
        job.updated_by = current_user.id
        db.flush()
        db.refresh(row)
    return _asset_response(row)


async def get_asset(db: Session, asset_id: int, current_user: User) -> MediaAssetResponse:
    row = crud_ingestion.get_asset(db, asset_id)
    if row is None:
        raise _not_found("媒体资产不存在")
    _owner_access(row.created_by, current_user)
    return _asset_response(row)


async def list_assets(
    db: Session,
    current_user: User,
    *,
    ingestion_job_id: int | None = None,
    sha256: str | None = None,
    scan_status: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> tuple[list[MediaAssetResponse], int]:
    if ingestion_job_id is not None:
        job = crud_ingestion.get_job(db, ingestion_job_id)
        if job is None:
            raise _not_found("接入作业不存在")
        _owner_access(job.created_by, current_user)
    normalized = str(scan_status).strip().upper() if scan_status else None
    if normalized and normalized not in {"PENDING", "SCANNING", "PASSED", "FAILED", "QUARANTINED"}:
        raise _error("扫描状态不受支持")
    rows, total = crud_ingestion.list_assets(
        db,
        user_id=current_user.id,
        is_superuser=is_task_superuser(current_user),
        ingestion_job_id=ingestion_job_id,
        sha256=sha256,
        scan_status=normalized,
        search=search,
        page=page,
        size=size,
    )
    return [_asset_response(row) for row in rows], total
