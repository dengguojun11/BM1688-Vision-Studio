"""BM1688 质量报告 / SOP 评估后台刷新（避免 quality-report HTTP 阻塞数十秒）。"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from labelu.internal.common.config import settings

_REFRESH_LOCK_GUARD = threading.Lock()
_REFRESH_LOCKS: Dict[int, threading.Lock] = {}


def _export_root(export_id: int) -> Path:
    return (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_BM1688_EXPORT_SUBDIR
        / str(int(export_id))
    )


def _status_path(export_id: int) -> Path:
    return _export_root(export_id) / "quality_report_refresh.json"


def _refresh_lock(export_id: int) -> threading.Lock:
    with _REFRESH_LOCK_GUARD:
        lock = _REFRESH_LOCKS.get(export_id)
        if lock is None:
            lock = threading.Lock()
            _REFRESH_LOCKS[export_id] = lock
        return lock


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_refresh_status(export_id: int) -> Optional[Dict[str, Any]]:
    path = _status_path(export_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_status(export_id: int, payload: Dict[str, Any]) -> None:
    path = _status_path(export_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_refresh_running(export_id: int) -> bool:
    data = read_refresh_status(export_id)
    return bool(data and str(data.get("status") or "").upper() == "RUNNING")


def start_quality_report_refresh_background(
    export_id: int,
    *,
    run_onnx_val: bool = False,
) -> Dict[str, Any]:
    """若已在跑则返回 busy；否则后台线程执行 SOP + quality report。"""
    lock = _refresh_lock(export_id)
    if not lock.acquire(blocking=False):
        existing = read_refresh_status(export_id) or {}
        return {
            "status": "busy",
            "export_id": export_id,
            "refresh_status": existing.get("status") or "RUNNING",
            "started_at": existing.get("started_at"),
            "run_onnx_val": existing.get("run_onnx_val"),
        }

    started = _now_iso()
    _write_status(
        export_id,
        {
            "export_id": export_id,
            "status": "RUNNING",
            "started_at": started,
            "finished_at": None,
            "run_onnx_val": bool(run_onnx_val),
            "error_message": None,
        },
    )

    def _worker() -> None:
        err: Optional[str] = None
        try:
            from labelu.internal.application.service.yolo_bm1688_export import (
                _generate_quality_report_sync,
            )
            from labelu.internal.application.service.yolo_sop_model_evaluation import (
                run_server_sop_evaluations_for_export,
            )
            from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
            from labelu.internal.common.db import session_scope

            with session_scope() as db:
                row = crud_yolo_bm1688_export.get(db, export_id)
                if row and row.status == "COMPLETED":
                    try:
                        run_server_sop_evaluations_for_export(
                            export_id,
                            yolo_train_job_id=int(row.yolo_train_job_id),
                            imgsz=int(row.imgsz or 640),
                        )
                    except Exception:
                        logger.exception(
                            "SOP server eval refresh export_id={}", export_id
                        )
            _generate_quality_report_sync(export_id, run_onnx_val=run_onnx_val)
        except Exception as exc:
            err = str(exc)[:4000]
            logger.exception("quality_report refresh export_id={}", export_id)
        finally:
            _write_status(
                export_id,
                {
                    "export_id": export_id,
                    "status": "FAILED" if err else "COMPLETED",
                    "started_at": started,
                    "finished_at": _now_iso(),
                    "run_onnx_val": bool(run_onnx_val),
                    "error_message": err,
                },
            )
            lock.release()

    threading.Thread(
        target=_worker,
        name=f"bm1688-quality-refresh-{export_id}",
        daemon=True,
    ).start()
    return {
        "status": "started",
        "export_id": export_id,
        "refresh_status": "RUNNING",
        "started_at": started,
        "run_onnx_val": bool(run_onnx_val),
    }
