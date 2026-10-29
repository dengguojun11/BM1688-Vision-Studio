from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.application.service.attachment import (
    EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE,
    is_edge_validation_external_attachment_path,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.storage import build_thumbnail_key, get_storage_backend

_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()


def _retention_cutoff() -> datetime:
    hours = max(1, int(settings.YOLO_EDGE_VALIDATION_EXTERNAL_RETENTION_HOURS))
    return datetime.now() - timedelta(hours=hours)


def cleanup_stale_edge_validation_external_attachments(*, now: datetime | None = None) -> dict:
    if not settings.YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_ENABLED:
        return {
            "enabled": False,
            "soft_deleted_rows_removed": 0,
            "files_removed": 0,
            "thumbnails_removed": 0,
            "orphan_files_removed": 0,
            "orphan_thumbnails_removed": 0,
        }

    storage = get_storage_backend()
    if storage.is_remote:
        logger.info("edge validation external cleanup skipped for remote storage backend")
        return {
            "enabled": True,
            "remote_storage_skipped": True,
            "soft_deleted_rows_removed": 0,
            "files_removed": 0,
            "thumbnails_removed": 0,
            "orphan_files_removed": 0,
            "orphan_thumbnails_removed": 0,
        }

    cutoff = now or _retention_cutoff()
    summary = {
        "enabled": True,
        "soft_deleted_rows_removed": 0,
        "files_removed": 0,
        "thumbnails_removed": 0,
        "orphan_files_removed": 0,
        "orphan_thumbnails_removed": 0,
    }

    with session_scope() as db:
        stale_rows = crud_attachment.list_soft_deleted_edge_validation_external_before(
            db, deleted_before=cutoff
        )
        stale_ids = [int(row.id) for row in stale_rows]
        for row in stale_rows:
            try:
                if getattr(row, "path", None):
                    storage.delete(str(row.path))
                    summary["files_removed"] += 1
                    thumbnail_key = build_thumbnail_key(str(row.path))
                    if storage.exists(thumbnail_key):
                        storage.delete(thumbnail_key)
                        summary["thumbnails_removed"] += 1
            except Exception as exc:
                logger.warning(
                    "edge validation external cleanup file delete failed attachment_id={} err={}",
                    getattr(row, "id", None),
                    exc,
                )

        if stale_ids:
            with begin_transaction(db):
                crud_attachment.hard_delete_by_ids(db, attachment_ids=stale_ids)
            summary["soft_deleted_rows_removed"] = len(stale_ids)

    media_root = Path(settings.MEDIA_ROOT)
    upload_root = media_root / settings.UPLOAD_DIR
    if not upload_root.is_dir():
        return summary

    active_paths: set[str] = set()
    with session_scope() as db:
        for row in crud_attachment.list_active_edge_validation_external(db):
            rel = str(getattr(row, "path", "") or "").replace("\\", "/").strip("/")
            if rel:
                active_paths.add(rel)
                active_paths.add(build_thumbnail_key(rel).replace("\\", "/").strip("/"))

    for file_path in upload_root.rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(media_root).as_posix()
        if not is_edge_validation_external_attachment_path(rel):
            continue
        if rel in active_paths:
            continue
        try:
            if file_path.stat().st_mtime > cutoff.timestamp():
                continue
        except OSError:
            continue
        try:
            file_path.unlink(missing_ok=True)
            if rel.endswith("-thumbnail" + file_path.suffix):
                summary["orphan_thumbnails_removed"] += 1
            else:
                summary["orphan_files_removed"] += 1
        except Exception as exc:
            logger.warning("edge validation external cleanup orphan delete failed path={} err={}", rel, exc)
    return summary


def schedule_edge_validation_external_cleanup_watchdog() -> None:
    global _WATCHDOG_STARTED
    if not settings.YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_ENABLED:
        return
    interval = max(300, int(settings.YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_INTERVAL_SECONDS))

    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _tick() -> None:
        try:
            summary = cleanup_stale_edge_validation_external_attachments()
            logger.info(
                "edge validation external cleanup tick finished rows={} files={} thumbs={} orphan_files={} orphan_thumbs={}",
                summary.get("soft_deleted_rows_removed", 0),
                summary.get("files_removed", 0),
                summary.get("thumbnails_removed", 0),
                summary.get("orphan_files_removed", 0),
                summary.get("orphan_thumbnails_removed", 0),
            )
        except Exception:
            logger.exception("edge validation external cleanup watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    threading.Thread(
        target=cleanup_stale_edge_validation_external_attachments,
        daemon=True,
        name="edge-validation-external-cleanup-bootstrap",
    ).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info(
        "edge validation external cleanup watchdog started (interval={}s, retention={}h, purpose={})",
        interval,
        int(settings.YOLO_EDGE_VALIDATION_EXTERNAL_RETENTION_HOURS),
        EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE,
    )
