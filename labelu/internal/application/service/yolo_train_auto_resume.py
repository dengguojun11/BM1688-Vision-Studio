"""非人为暂停的训练作业：检测后可从 last.pt 自动继续运行。"""

from __future__ import annotations

import threading
from typing import List

from loguru import logger
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()


def _statuses_to_scan() -> tuple[str, ...]:
    return (
        YoloTrainStatus.CANCELLED.value,
        YoloTrainStatus.PROCESSING.value,
        YoloTrainStatus.PENDING.value,
    )


def _is_automatic_training_job(job: YoloTrainJob) -> bool:
    return (
        str(getattr(job, "queue_source", None) or "MANUAL").strip().upper() == "AUTO"
        or getattr(job, "auto_training_plan_id", None) is not None
        or getattr(job, "auto_training_round_id", None) is not None
    )


def scan_and_auto_resume_all_jobs() -> int:
    """扫描可恢复的作业并提交续训，返回本次触发续训的数量。"""
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_AUTO_RESUME_TRAIN_ENABLED:
        return 0

    job_ids: List[int] = []
    with session_scope() as db:
        query = db.query(YoloTrainJob.id).filter(
            YoloTrainJob.status.in_(_statuses_to_scan())
        )
        if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
            query = query.filter(
                and_(
                    func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL"))
                    != "AUTO",
                    YoloTrainJob.auto_training_plan_id.is_(None),
                    YoloTrainJob.auto_training_round_id.is_(None),
                )
            )
        rows = query.order_by(YoloTrainJob.id.asc()).all()
        job_ids = [int(r[0]) for r in rows]

    resumed = 0
    for jid in job_ids:
        if maybe_auto_resume_train_job(jid):
            resumed += 1
    if resumed:
        logger.info("auto-resume train: submitted {} job(s)", resumed)
    return resumed


def maybe_auto_resume_train_job(job_id: int) -> bool:
    from labelu.internal.application.service.yolo_train import (
        _ACTIVE_TRAIN_JOBS,
        _resume_train_job_from_checkpoint,
        is_user_pause_requested,
        job_can_auto_resume,
    )

    try:
        from labelu.internal.common.yolo_train_deploy import is_deploy_pause_in_progress

        if is_deploy_pause_in_progress():
            return False
    except Exception:
        pass

    jid = int(job_id)
    if jid in _ACTIVE_TRAIN_JOBS:
        return False

    status_log = ""
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=jid)
        if not job or not job_can_auto_resume(job):
            return False
        if (
            not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
            and _is_automatic_training_job(job)
        ):
            return False
        if is_user_pause_requested(jid):
            return False
        status_log = str(job.status or "")
        ok = _resume_train_job_from_checkpoint(db, jid)
    if ok:
        logger.info(
            "auto-resume train job {} (status={}, not user pause)",
            jid,
            status_log,
        )
    return ok


def schedule_train_auto_resume_watchdog() -> None:
    """启动时立即扫描一次，之后按间隔周期性扫描中断的训练。"""
    global _WATCHDOG_STARTED
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_AUTO_RESUME_TRAIN_ENABLED:
        return
    interval = max(30, int(settings.YOLO_AUTO_RESUME_SCAN_INTERVAL_SECONDS))

    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _tick() -> None:
        try:
            scan_and_auto_resume_all_jobs()
        except Exception:
            logger.exception("train auto-resume watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    threading.Thread(target=scan_and_auto_resume_all_jobs, daemon=True).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info("train auto-resume watchdog started (interval={}s)", interval)
