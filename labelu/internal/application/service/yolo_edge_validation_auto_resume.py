"""部署重启后自动恢复卡死在 PROCESSING 的边端 SSH 验证任务。"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import List

from sqlalchemy import and_, or_
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_edge_validation_job
from labelu.internal.application.service.yolo_edge_validation import (
    can_resume_edge_validation_job,
    is_stale_edge_validation_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import session_scope
from labelu.internal.common import edge_device_registry
from labelu.internal.common.execution_fencing import execution_lease_is_active
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()


def _registered_board_agent_job(row: object) -> bool:
    """Return whether a stale row belongs to the pull-based Board Agent.

    Recovery invokes the legacy SSH runner, so it must never take over a job
    whose registered device is owned by the Agent pull channel.  Missing
    records remain compatible with pre-registry jobs; a registry read error is
    fail-closed because the channel cannot be established safely.
    """

    device_id = str(getattr(row, "edge_device_id", None) or "").strip()
    if not device_id:
        return False
    try:
        record = edge_device_registry.find_device_record(device_id)
    except Exception:
        logger.exception(
            "读取边端设备通道失败，跳过自动 SSH 恢复 job_id={} device_id={}",
            getattr(row, "id", None),
            device_id,
        )
        return True
    return bool(
        isinstance(record, dict)
        and str(record.get("validation_channel") or "ssh").strip().lower()
        == "board_agent"
    )


def _list_resume_candidate_job_ids(db: Session) -> List[int]:
    query = db.query(YoloEdgeValidationJob.id)
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        query = query.filter(YoloEdgeValidationJob.auto_training_round_id.is_(None))
    rows = (
        query
        .filter(
            or_(
                YoloEdgeValidationJob.status == "PROCESSING",
                and_(
                    YoloEdgeValidationJob.status == "PENDING",
                    or_(
                        YoloEdgeValidationJob.queued_at.isnot(None),
                        YoloEdgeValidationJob.threshold_source == "strategy_evolution",
                    ),
                ),
            )
        )
        .order_by(YoloEdgeValidationJob.id.asc())
        .all()
    )
    return [int(r[0]) for r in rows]


def maybe_auto_resume_edge_validation_job(job_id: int, *, now: datetime | None = None) -> bool:
    from labelu.internal.application.service.yolo_edge_validation import _run_manual_job_sync

    jid = int(job_id)
    recovery_owner: str | None = None
    with session_scope() as db:
        row = crud_yolo_edge_validation_job.get(db, jid)
        if not row:
            return False
        if execution_lease_is_active(row, now=now or datetime.now()):
            logger.info(
                "skip edge validation recovery with active execution lease job_id={}",
                jid,
            )
            return False
        if (
            getattr(row, "auto_training_round_id", None) is not None
            and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
        ):
            return False
        status_value = str(getattr(row, "status", "") or "").strip().upper()
        threshold_source = str(getattr(row, "threshold_source", "") or "").strip()
        if status_value == "PROCESSING" and _registered_board_agent_job(row):
            logger.info(
                "skip legacy SSH recovery for Board Agent job_id={} device_id={}",
                jid,
                getattr(row, "edge_device_id", None),
            )
            return False
        if status_value == "PROCESSING":
            if not is_stale_edge_validation_job(row, now=now):
                return False
        elif not (
            status_value == "PENDING"
            and (
                getattr(row, "queued_at", None) is not None
                or threshold_source == "strategy_evolution"
            )
            and can_resume_edge_validation_job(row)
        ):
            return False
        export_id = int(row.export_id)
        stale_ref = row.started_at or row.updated_at
        reason = (
            "stale_processing_recovery"
            if status_value == "PROCESSING"
            else "strategy_evolution_pending_resume"
        )

        # Automatic recovery must be fenced in the database before starting a
        # worker thread. Manual jobs keep the compatibility path below.
        is_queued_pending = (
            status_value == "PENDING"
            and getattr(row, "queued_at", None) is not None
        )
        if (
            not is_queued_pending
            and getattr(row, "auto_training_round_id", None) is not None
            and str(getattr(row, "pipeline_origin", "") or "")
            in {"AUTO_QUALITY", "MANUAL_DIAGNOSTIC"}
        ):
            from labelu.internal.application.service.auto_training_dispatch import (
                claim_edge_validation_recovery,
            )

            claimed, recovery_owner = claim_edge_validation_recovery(
                db,
                edge_job_id=jid,
                status=status_value,
                now=now or datetime.now(),
            )
            if not claimed:
                return False

    if status_value == "PENDING" and getattr(row, "queued_at", None) is not None:
        from labelu.internal.application.service import yolo_edge_validation_scheduler

        yolo_edge_validation_scheduler.schedule_edge_validation_dispatch()
        return True

    logger.info(
        "queue edge validation recovery job {} (export_id={}, ref={}, reason={})",
        jid,
        export_id,
        stale_ref,
        reason,
    )
    def _run() -> None:
        try:
            _run_manual_job_sync(jid)
        finally:
            if recovery_owner:
                from labelu.internal.application.service.auto_training_dispatch import (
                    release_edge_validation_recovery,
                )

                try:
                    with session_scope() as release_db:
                        release_edge_validation_recovery(
                            release_db,
                            edge_job_id=jid,
                            owner=recovery_owner,
                        )
                except Exception:
                    logger.exception(
                        "failed to release edge recovery lease job_id={}", jid
                    )

    try:
        threading.Thread(
            target=_run,
            args=(),
            daemon=True,
            name=f"yolo-edge-validation-resume-{jid}",
        ).start()
    except Exception:
        if recovery_owner:
            from labelu.internal.application.service.auto_training_dispatch import (
                release_edge_validation_recovery,
            )

            try:
                with session_scope() as release_db:
                    release_edge_validation_recovery(
                        release_db,
                        edge_job_id=jid,
                        owner=recovery_owner,
                    )
            except Exception:
                logger.exception(
                    "failed to release edge recovery lease after thread start failure job_id={}",
                    jid,
                )
        raise
    return True


def scan_and_auto_resume_edge_validation_jobs(*, now: datetime | None = None) -> int:
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_EDGE_VALIDATION_AUTO_RESUME_ENABLED:
        return 0

    with session_scope() as db:
        job_ids = _list_resume_candidate_job_ids(db)

    resumed = 0
    for jid in job_ids:
        if maybe_auto_resume_edge_validation_job(jid, now=now):
            resumed += 1
    if resumed:
        logger.info("edge validation recovery: queued {} job(s)", resumed)
    from labelu.internal.application.service import yolo_edge_validation_scheduler

    yolo_edge_validation_scheduler.schedule_edge_validation_dispatch()
    return resumed


def schedule_edge_validation_auto_resume_watchdog() -> None:
    """启动时立即扫描一次，之后按间隔恢复 PROCESSING 卡死的边端验证。"""
    global _WATCHDOG_STARTED
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_EDGE_VALIDATION_AUTO_RESUME_ENABLED:
        return
    interval = max(
        30,
        int(settings.YOLO_EDGE_VALIDATION_AUTO_RESUME_SCAN_INTERVAL_SECONDS),
    )

    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _tick() -> None:
        try:
            scan_and_auto_resume_edge_validation_jobs()
        except Exception:
            logger.exception("edge validation auto-resume watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    threading.Thread(target=scan_and_auto_resume_edge_validation_jobs, daemon=True).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info(
        "edge validation auto-resume watchdog started (interval={}s, stale_minutes={})",
        interval,
        settings.YOLO_EDGE_VALIDATION_STALE_MINUTES,
    )
