"""Automatically repair missing post-training quality evidence.

The release gate is intentionally conservative when test-set or same-batch
PT/BModel evidence is missing.  This watchdog fills those artifacts in the
background and leaves the item in manual review only after bounded retries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
from typing import Any

from loguru import logger
from sqlalchemy import and_, func

from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service import edge_ssh_validation
from labelu.internal.application.service import yolo_edge_validation_scheduler
from labelu.internal.common.config import settings
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_WATCHDOG_LOCK = threading.Lock()
_WATCHDOG_STARTED = False
_ACTIVE_TRAIN_RECOVERY: set[int] = set()
_ACTIVE_EDGE_RECOVERY: set[int] = set()


def _now() -> datetime:
    return datetime.now()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _state_ready(state: dict[str, Any], now: datetime) -> bool:
    if bool(state.get("running")):
        return False
    retry_at = state.get("retry_at")
    if not retry_at:
        return True
    try:
        return now >= datetime.fromisoformat(str(retry_at))
    except (TypeError, ValueError):
        return True


def _state_path_for_train(job_id: int) -> Path:
    return (
        yolo_train_service._job_work_root(int(job_id)) / ".quality_evidence_auto.json"
    )


def _export_root(export_id: int) -> Path:
    from labelu.internal.application.service.yolo_bm1688_export import (
        _export_dir_for_row,
    )

    return _export_dir_for_row(int(export_id))


def _state_path_for_edge(export_id: int) -> Path:
    return _export_root(int(export_id)) / ".quality_evidence_edge_auto.json"


def _test_val_missing(job: YoloTrainJob) -> bool:
    if str(job.status or "") != YoloTrainStatus.COMPLETED.value:
        return False
    if str(job.job_type or "") not in {"yolo_detection", "plate_detection"}:
        return False
    if not str(job.weights_path or "").strip():
        return False
    metrics = yolo_train_service._test_val_metrics_for_job(job)
    if metrics is None:
        return True
    status = (
        metrics.get("status")
        if isinstance(metrics, dict)
        else getattr(metrics, "status", None)
    )
    return str(status or "").strip().lower() != "ok"


def _test_val_evidence_ready(job_id: int) -> bool:
    with session_scope() as db:
        job = db.query(YoloTrainJob).filter(YoloTrainJob.id == int(job_id)).first()
        return bool(job and not _test_val_missing(job))


def _within_lookback(created_at: datetime | None, now: datetime) -> bool:
    if created_at is None:
        return True
    days = max(1, int(settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_LOOKBACK_DAYS))
    return created_at >= now - timedelta(days=days)


def _has_automatic_training_lineage(row: Any) -> bool:
    return (
        str(getattr(row, "queue_source", None) or "MANUAL").strip().upper() == "AUTO"
        or getattr(row, "auto_training_plan_id", None) is not None
        or getattr(row, "auto_training_round_id", None) is not None
    )


def _mark_train_state(job_id: int, state: dict[str, Any]) -> None:
    _write_json(_state_path_for_train(job_id), state)


def _mark_edge_state(export_id: int, state: dict[str, Any]) -> None:
    _write_json(_state_path_for_edge(export_id), state)


def _run_train_recovery(job_id: int, task_id: int) -> None:
    try:
        yolo_train_service._run_test_val_sync(int(job_id), int(task_id))
        if not _test_val_evidence_ready(job_id):
            raise RuntimeError("test-val validation completed without usable evidence")
    except Exception as exc:
        logger.warning(
            "quality evidence test-val recovery failed job {}: {}", job_id, exc
        )
        state = _load_json(_state_path_for_train(job_id))
        state.update(
            {
                "running": False,
                "last_status": "failed",
                "last_error": str(exc)[:4000],
                "last_finished_at": _now().isoformat(),
                "retry_at": (
                    _now()
                    + timedelta(
                        seconds=int(
                            settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_RETRY_DELAY_SECONDS
                        )
                    )
                ).isoformat(),
            }
        )
        _mark_train_state(job_id, state)
    else:
        state = _load_json(_state_path_for_train(job_id))
        state.update(
            {
                "running": False,
                "last_status": "completed",
                "last_error": None,
                "last_finished_at": _now().isoformat(),
                "retry_at": None,
            }
        )
        _mark_train_state(job_id, state)
    finally:
        _ACTIVE_TRAIN_RECOVERY.discard(int(job_id))


def maybe_recover_test_val(job_id: int, *, now: datetime | None = None) -> bool:
    now = now or _now()
    jid = int(job_id)
    with _WATCHDOG_LOCK:
        if jid in _ACTIVE_TRAIN_RECOVERY:
            return False
        with session_scope() as db:
            job = db.query(YoloTrainJob).filter(YoloTrainJob.id == jid).first()
            if (
                not job
                or not _within_lookback(job.created_at, now)
                or not _test_val_missing(job)
            ):
                return False
            if (
                not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
                and _has_automatic_training_lineage(job)
            ):
                return False
            task_id = int(job.task_id or 0)
            if task_id <= 0:
                return False
        path = _state_path_for_train(jid)
        state = _load_json(path)
        attempts = int(state.get("attempts") or 0)
        limit = max(1, int(settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_MAX_ATTEMPTS))
        if attempts >= limit or not _state_ready(state, now):
            return False
        attempts += 1
        state.update(
            {
                "attempts": attempts,
                "max_attempts": limit,
                "running": True,
                "last_started_at": now.isoformat(),
                "retry_at": None,
            }
        )
        _mark_train_state(jid, state)
        _ACTIVE_TRAIN_RECOVERY.add(jid)

    threading.Thread(
        target=_run_train_recovery,
        args=(jid, task_id),
        name=f"quality-test-val-{jid}",
        daemon=True,
    ).start()
    return True


def _edge_comparison_missing(
    edge: YoloEdgeValidationJob, train_job: YoloTrainJob
) -> bool:
    if str(train_job.job_type or "") != "plate_detection":
        return False
    if str(edge.status or "").upper() not in {"COMPLETED", "FAILED"}:
        return False
    if str(edge.mode or "") != "run_and_eval":
        return False
    try:
        result = json.loads(edge.result_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {}
    return not isinstance(result.get("comparison_pt_bmodel"), dict)


def _sync_edge_recovery_state(export_id: int, edge: YoloEdgeValidationJob) -> None:
    path = _state_path_for_edge(int(export_id))
    state = _load_json(path)
    if not state or not state.get("running"):
        return
    try:
        result = json.loads(edge.result_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {}
    has_comparison = isinstance(result.get("comparison_pt_bmodel"), dict)
    status = str(edge.status or "").upper()
    if status == "COMPLETED" and has_comparison:
        state.update(
            {
                "running": False,
                "last_status": "completed",
                "last_error": None,
                "last_finished_at": _now().isoformat(),
                "retry_at": None,
            }
        )
        _ACTIVE_EDGE_RECOVERY.discard(int(export_id))
        _write_json(path, state)
    elif status in {"COMPLETED", "FAILED"}:
        state.update(
            {
                "running": False,
                "last_status": "failed",
                "last_error": str(
                    edge.error_message
                    or "edge validation completed without PT/BModel comparison"
                )[:4000],
                "last_finished_at": _now().isoformat(),
                "retry_at": (
                    _now()
                    + timedelta(
                        seconds=int(
                            settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_RETRY_DELAY_SECONDS
                        )
                    )
                ).isoformat(),
            }
        )
        _ACTIVE_EDGE_RECOVERY.discard(int(export_id))
        _write_json(path, state)


def maybe_recover_edge_comparison(
    export_id: int, *, now: datetime | None = None
) -> bool:
    now = now or _now()
    eid = int(export_id)
    with _WATCHDOG_LOCK:
        with session_scope() as db:
            export = (
                db.query(YoloBm1688Export).filter(YoloBm1688Export.id == eid).first()
            )
            if not export or str(export.status or "").upper() != "COMPLETED":
                return False
            train_job = (
                db.query(YoloTrainJob)
                .filter(YoloTrainJob.id == export.yolo_train_job_id)
                .first()
            )
            edge = (
                db.query(YoloEdgeValidationJob)
                .filter(YoloEdgeValidationJob.export_id == eid)
                .order_by(YoloEdgeValidationJob.id.desc())
                .first()
            )
            if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED and any(
                _has_automatic_training_lineage(row)
                for row in (export, train_job, edge)
                if row is not None
            ):
                return False
            if edge:
                _sync_edge_recovery_state(eid, edge)
            if (
                not train_job
                or not edge
                or not _edge_comparison_missing(edge, train_job)
            ):
                return False
            if (
                eid in _ACTIVE_EDGE_RECOVERY
                or not edge_ssh_validation._edge_ssh_configured()
                and not yolo_edge_validation_scheduler.has_configured_edge_devices()
            ):
                return False
        path = _state_path_for_edge(eid)
        state = _load_json(path)
        attempts = int(state.get("attempts") or 0)
        limit = max(1, int(settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_MAX_ATTEMPTS))
        if attempts >= limit or not _state_ready(state, now):
            return False
        attempts += 1
        state.update(
            {
                "attempts": attempts,
                "max_attempts": limit,
                "running": True,
                "last_started_at": now.isoformat(),
                "retry_at": None,
            }
        )
        _mark_edge_state(eid, state)
        _ACTIVE_EDGE_RECOVERY.add(eid)
        queued = yolo_edge_validation_scheduler.enqueue_edge_validation_job(
            int(edge.id)
        )
        if not queued:
            _ACTIVE_EDGE_RECOVERY.discard(eid)
            state["running"] = False
            state["last_status"] = "failed"
            state["last_error"] = "edge validation queue rejected the job"
            _mark_edge_state(eid, state)
            return False

    return True


def scan_and_recover_quality_evidence() -> dict[str, int]:
    if (
        not settings.YOLO_TRAIN_ENABLED
        or not settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_ENABLED
    ):
        return {"test_val": 0, "edge": 0}
    now = _now()
    batch = max(1, int(settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_BATCH_SIZE))
    with session_scope() as db:
        candidate_limit = max(batch * 20, 100)
        train_query = db.query(YoloTrainJob.id).filter(
            YoloTrainJob.status == YoloTrainStatus.COMPLETED.value
        )
        export_query = db.query(YoloBm1688Export.id).filter(
            YoloBm1688Export.status == "COMPLETED"
        )
        if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
            train_query = train_query.filter(
                and_(
                    func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL"))
                    != "AUTO",
                    YoloTrainJob.auto_training_plan_id.is_(None),
                    YoloTrainJob.auto_training_round_id.is_(None),
                )
            )
            export_query = export_query.filter(
                YoloBm1688Export.auto_training_plan_id.is_(None),
                YoloBm1688Export.auto_training_round_id.is_(None),
            )
        train_ids = [
            int(value)
            for (value,) in train_query.order_by(YoloTrainJob.id.desc())
            .limit(candidate_limit)
            .all()
        ]
        export_ids = [
            int(value)
            for (value,) in export_query.order_by(YoloBm1688Export.id.desc())
            .limit(candidate_limit)
            .all()
        ]
    test_val = sum(
        1 for jid in train_ids[:batch] if maybe_recover_test_val(jid, now=now)
    )
    edge = sum(
        1 for eid in export_ids[:batch] if maybe_recover_edge_comparison(eid, now=now)
    )
    if test_val or edge:
        logger.info(
            "quality evidence recovery queued test_val={} edge={}", test_val, edge
        )
    return {"test_val": test_val, "edge": edge}


def schedule_quality_evidence_auto_recovery() -> None:
    global _WATCHDOG_STARTED
    if (
        not settings.YOLO_TRAIN_ENABLED
        or not settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_ENABLED
    ):
        return
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True
    interval = max(
        30, int(settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_INTERVAL_SECONDS)
    )

    def _tick() -> None:
        try:
            scan_and_recover_quality_evidence()
        except Exception:
            logger.exception("quality evidence recovery watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    threading.Thread(target=scan_and_recover_quality_evidence, daemon=True).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info(
        "quality evidence recovery watchdog started interval={}s max_attempts={}",
        interval,
        settings.YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_MAX_ATTEMPTS,
    )
