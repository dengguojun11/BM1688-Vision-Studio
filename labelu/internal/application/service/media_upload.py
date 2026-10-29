from __future__ import annotations

import hashlib
import json
import math
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import aiofiles
from fastapi import status, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from labelu.internal.adapter.persistence import crud_attachment, crud_media_upload, crud_task
from labelu.internal.application.command.media_upload import CreateMediaUploadSessionCommand
from labelu.internal.application.response.media_upload import MediaUploadSessionResponse
from labelu.internal.application.service.attachment import _sanitize_attachment_filename
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.media_scan import scan_media_path
from labelu.internal.common.storage import build_absolute_media_url, build_attachment_api_path, get_storage_backend
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.media_upload import MediaUploadSession
from labelu.internal.domain.models.user import User


def _error(message: str, http_status: int = status.HTTP_400_BAD_REQUEST):
    return LabelUException(code=ErrorCode.CODE_30002_VALIDATION_ERROR, status_code=http_status, message=message)


def _staging_path(session_id: str) -> Path:
    root = Path(settings.MEDIA_ROOT) / ".labelu_upload_staging"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"chunk-{session_id}.part"


def _quarantine_path(session_id: str, filename: str) -> Path:
    root = Path(settings.MEDIA_ROOT) / ".labelu_quarantine"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{session_id}-{_sanitize_attachment_filename(filename)}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _received(row: MediaUploadSession) -> list[int]:
    try:
        value = json.loads(row.received_chunks_json or "[]")
        return sorted({int(item) for item in value})
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _response(row: MediaUploadSession) -> MediaUploadSessionResponse:
    return MediaUploadSessionResponse(
        session_id=row.session_id, task_id=row.task_id, filename=row.filename,
        total_size=row.total_size, chunk_size=row.chunk_size, total_chunks=row.total_chunks,
        received_chunks=_received(row), status=row.status, sha256=row.sha256,
        attachment_id=row.attachment_id,
        scan_status=row.scan_status,
        scan_provider=row.scan_provider,
        scan_signature=row.scan_signature,
        scan_error=row.scan_error,
        scanned_at=row.scanned_at,
        quarantined_at=row.quarantined_at,
        quarantine_until=row.quarantine_until,
    )


def _get_owned(db: Session, session_id: str, user: User) -> MediaUploadSession:
    row = crud_media_upload.get(db, session_id)
    if row is None or row.created_by != user.id:
        raise LabelUException(code=ErrorCode.CODE_30001_NO_PERMISSION, status_code=status.HTTP_403_FORBIDDEN)
    return row


def get_session(db: Session, session_id: str, user: User) -> MediaUploadSessionResponse:
    return _response(_get_owned(db, session_id, user))


async def create_session(db: Session, cmd: CreateMediaUploadSessionCommand, user: User) -> MediaUploadSessionResponse:
    task = crud_task.get(db=db, task_id=cmd.task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    assert_user_has_task_access(task, user)
    total_chunks = math.ceil(cmd.total_size / cmd.chunk_size)
    row = MediaUploadSession(
        session_id=uuid.uuid4().hex, task_id=cmd.task_id,
        filename=_sanitize_attachment_filename(cmd.filename), total_size=cmd.total_size,
        chunk_size=cmd.chunk_size, total_chunks=total_chunks,
        received_chunks_json="[]", sha256=cmd.sha256, status="UPLOADING", created_by=user.id,
    )
    with begin_transaction(db):
        crud_media_upload.create(db, row)
    path = _staging_path(row.session_id)
    with path.open("wb") as stream:
        stream.truncate(cmd.total_size)
    return _response(row)


async def upload_chunk(db: Session, session_id: str, chunk_no: int, file: UploadFile, user: User) -> MediaUploadSessionResponse:
    row = _get_owned(db, session_id, user)
    if row.status != "UPLOADING":
        raise _error("上传会话已结束", status.HTTP_409_CONFLICT)
    if chunk_no < 0 or chunk_no >= row.total_chunks:
        raise _error("分片序号超出范围")
    data = await file.read(row.chunk_size + 1)
    expected = min(row.chunk_size, row.total_size - chunk_no * row.chunk_size)
    if len(data) != expected:
        raise _error(f"分片大小错误，应为 {expected} 字节")
    async with aiofiles.open(_staging_path(row.session_id), "r+b") as stream:
        await stream.seek(chunk_no * row.chunk_size)
        await stream.write(data)
    received = set(_received(row))
    received.add(chunk_no)
    with begin_transaction(db):
        row.received_chunks_json = json.dumps(sorted(received), separators=(",", ":"))
        db.flush()
    return _response(row)


async def complete(db: Session, session_id: str, user: User) -> MediaUploadSessionResponse:
    row = _get_owned(db, session_id, user)
    if row.status in {"COMPLETED", "DUPLICATE"}:
        return _response(row)
    if row.status in {"SCANNING", "QUARANTINED", "BLOCKED", "SCAN_FAILED"}:
        raise _error("上传文件正在扫描或已被隔离", status.HTTP_409_CONFLICT)
    if row.status != "UPLOADING":
        raise _error("上传会话状态不可完成", status.HTTP_409_CONFLICT)
    if len(_received(row)) != row.total_chunks:
        raise _error("仍有分片未上传", status.HTTP_409_CONFLICT)
    path = _staging_path(row.session_id)
    if not path.is_file() or path.stat().st_size != row.total_size:
        raise _error("上传文件大小校验失败")
    digest = await run_in_threadpool(_sha256_file, path)
    if row.sha256 and row.sha256 != digest:
        raise _error("文件摘要校验失败", status.HTTP_422_UNPROCESSABLE_ENTITY)
    with begin_transaction(db):
        row.sha256 = digest
        row.status = "SCANNING"
        row.scan_status = "PENDING"
        row.scan_provider = None
        row.scan_signature = None
        row.scan_error = None
        row.scanned_at = None
        row.quarantined_at = None
        row.quarantine_path = None
        row.quarantine_until = None
        db.flush()

    scan = await run_in_threadpool(scan_media_path, path)
    scan_signature = getattr(scan, "signature", None)
    blocked = scan.status == "INFECTED" or (
        scan.status in {"ERROR", "NOT_CONFIGURED"}
        and settings.MEDIA_VIRUS_SCAN_REQUIRED
    )
    if blocked:
        quarantine = _quarantine_path(row.session_id, row.filename)
        quarantine_error = None
        try:
            shutil.move(str(path), str(quarantine))
        except OSError as exc:
            quarantine_error = f"文件隔离失败: {exc}"
        with begin_transaction(db):
            row.status = "QUARANTINED" if scan.status == "INFECTED" else "BLOCKED"
            row.scan_status = scan.status
            row.scan_provider = scan.provider
            row.scan_signature = scan_signature
            row.scan_error = quarantine_error or scan.error
            row.scanned_at = scan.scanned_at
            row.quarantined_at = datetime.now()
            if quarantine_error is None:
                row.quarantine_path = str(quarantine)
                row.quarantine_until = datetime.now() + timedelta(
                    hours=int(settings.MEDIA_VIRUS_QUARANTINE_RETENTION_HOURS)
                )
            db.flush()
        return _response(row)

    with begin_transaction(db):
        row.scan_status = scan.status
        row.scan_provider = scan.provider
        row.scan_signature = scan_signature
        row.scan_error = scan.error
        row.scanned_at = scan.scanned_at
        db.flush()

    duplicate = crud_attachment.get_by_task_sha256(db, row.task_id, digest)
    if duplicate is not None:
        with begin_transaction(db):
            row.status = "DUPLICATE"
            row.attachment_id = duplicate.id
            row.scan_provider = row.scan_provider or "deduplication"
            row.completed_at = datetime.now()
            db.flush()
        path.unlink(missing_ok=True)
        return _response(row)

    storage = get_storage_backend()
    key = f"{settings.UPLOAD_DIR}/{row.task_id}/uploads/{row.session_id}-{row.filename}".replace("\\", "/")
    await run_in_threadpool(storage.save_file, path, key, None)
    attachment = TaskAttachment(
        path=key, url=build_attachment_api_path(key), filename=row.filename,
        created_by=user.id, updated_by=user.id, task_id=row.task_id,
        sha256=digest, size_bytes=row.total_size, content_type=file_content_type(row.filename),
    )
    with begin_transaction(db):
        db.add(attachment)
        db.flush()
        row.status = "COMPLETED"
        row.attachment_id = attachment.id
        row.completed_at = datetime.now()
        db.flush()
    return _response(row)


def cleanup_expired_quarantine(
    db: Session, *, now: datetime | None = None, limit: int = 200
) -> int:
    """Remove quarantine files after the configured review retention window."""
    current = now or datetime.now()
    rows = (
        db.query(MediaUploadSession)
        .filter(
            MediaUploadSession.status.in_(("QUARANTINED", "BLOCKED")),
            MediaUploadSession.quarantine_until.isnot(None),
            MediaUploadSession.quarantine_until <= current,
        )
        .order_by(MediaUploadSession.quarantine_until.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    cleaned = 0
    if rows:
        with begin_transaction(db):
            for row in rows:
                if row.quarantine_path:
                    Path(row.quarantine_path).unlink(missing_ok=True)
                row.quarantine_path = None
                row.quarantine_until = None
                row.scan_error = (
                    f"{row.scan_error}; 隔离文件已于 {current:%Y-%m-%d %H:%M:%S} 清理"
                    if row.scan_error
                    else f"隔离文件已于 {current:%Y-%m-%d %H:%M:%S} 清理"
                )[:2000]
                cleaned += 1
            db.flush()
    return cleaned


def file_content_type(filename: str) -> str | None:
    import mimetypes
    return mimetypes.guess_type(filename)[0]
