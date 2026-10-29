"""部署发版：自动暂停进行中的 YOLO 训练，重启后从队列恢复续训。"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_DEPLOY_ACTIVE_PHASES = frozenset(
    {
        "running",
        "finalizing",
        "val_per_class",
        "test_val",
        "loading_model",
        "preparing_dataset",
        "dataset_ready",
        "restarting",
    }
)

_DEPLOY_RESUME_THREAD_LOCK = threading.Lock()

_DEPLOY_PAUSE_DETAIL = "部署发版自动暂停"


def yolo_train_work_root() -> Path:
    return Path(settings.MEDIA_ROOT) / settings.YOLO_TRAIN_WORK_SUBDIR


def deploy_pause_lock_path() -> Path:
    return yolo_train_work_root() / ".deploy_pause_in_progress"


def deploy_resume_queue_path() -> Path:
    return yolo_train_work_root() / ".deploy_resume_queue.json"


def deploy_resume_claim_lock_path() -> Path:
    return yolo_train_work_root() / ".deploy_resume_claim.lock"


@contextmanager
def deploy_resume_claim_lock() -> Iterator[bool]:
    """Only one app/CLI process may submit the deploy resume queue."""
    if not _DEPLOY_RESUME_THREAD_LOCK.acquire(blocking=False):
        yield False
        return

    lock_file = None
    acquired = False
    try:
        root = yolo_train_work_root()
        root.mkdir(parents=True, exist_ok=True)
        lock_file = deploy_resume_claim_lock_path().open("a+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        acquired = True
        yield True
    finally:
        if acquired and lock_file is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                logger.warning("release deploy resume claim lock failed")
        if lock_file is not None:
            lock_file.close()
        _DEPLOY_RESUME_THREAD_LOCK.release()


def is_deploy_pause_in_progress() -> bool:
    return deploy_pause_lock_path().is_file()


def mark_deploy_pause_in_progress() -> None:
    root = yolo_train_work_root()
    root.mkdir(parents=True, exist_ok=True)
    deploy_pause_lock_path().write_text(
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        encoding="utf-8",
    )


def clear_deploy_pause_in_progress() -> None:
    try:
        deploy_pause_lock_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("clear deploy pause lock failed: {}", exc)


def load_deploy_resume_queue() -> List[int]:
    path = deploy_resume_queue_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("job_ids") or []
    out: List[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def save_deploy_resume_queue(job_ids: List[int]) -> None:
    root = yolo_train_work_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_ids": sorted({int(j) for j in job_ids}),
        "paused_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": "deploy",
    }
    deploy_resume_queue_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_deploy_resume_queue() -> None:
    try:
        deploy_resume_queue_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("clear deploy resume queue failed: {}", exc)


def _read_deploy_train_phase(job_id: int) -> tuple[Optional[str], Optional[int]]:
    """兼容 _read_train_phase 的扩展返回值，仅提取 deploy 需要的字段。"""
    from labelu.internal.application.service.yolo_train import _read_train_phase

    phase_info = _read_train_phase(int(job_id))
    phase = phase_info[0] if len(phase_info) > 0 else None
    runtime_pid = phase_info[2] if len(phase_info) > 2 else None
    return phase, runtime_pid


def job_is_active_for_deploy(job: YoloTrainJob) -> bool:
    """与 deploy 脚本 phase 检测一致：DB 排队/进行中且 phase 未终态。"""
    status = str(job.status or "")
    if status not in (
        YoloTrainStatus.PENDING.value,
        YoloTrainStatus.PROCESSING.value,
    ):
        return False
    phase, _pid = _read_deploy_train_phase(int(job.id))
    if phase in ("cancelled", "completed", "failed"):
        return False
    if phase in _DEPLOY_ACTIVE_PHASES:
        return True
    if status == YoloTrainStatus.PENDING.value:
        return True
    if status == YoloTrainStatus.PROCESSING.value:
        return phase is None or phase in _DEPLOY_ACTIVE_PHASES
    return False


def list_active_train_jobs(db: Session) -> List[YoloTrainJob]:
    rows = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.status.in_(
                (
                    YoloTrainStatus.PENDING.value,
                    YoloTrainStatus.PROCESSING.value,
                )
            )
        )
        .order_by(YoloTrainJob.id.asc())
        .all()
    )
    return [j for j in rows if job_is_active_for_deploy(j)]


def pause_train_job_for_deploy(db: Session, job_id: int) -> bool:
    """部署专用暂停：不写 user_pause，便于重启后自动续训。"""
    from labelu.internal.application.service.yolo_train import (
        _mark_job_cancelled,
        _terminate_train_process_tree,
        request_stop_train_job,
    )

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or not job_is_active_for_deploy(job):
        return False
    _phase, runtime_pid = _read_deploy_train_phase(job_id)
    request_stop_train_job(job_id)
    _terminate_train_process_tree(job_id, runtime_pid)
    _mark_job_cancelled(
        job_id,
        detail=_DEPLOY_PAUSE_DETAIL,
        db=db,
        skip_auto_resume=True,
    )
    logger.info("deploy pause: job_id={} phase={}", job_id, _phase)
    return True


def wait_jobs_paused_for_deploy(
    db: Session,
    job_ids: List[int],
    *,
    timeout_seconds: int = 3600,
    poll_interval: float = 3.0,
) -> bool:
    """等待作业进入已暂停/已停止状态。"""
    from labelu.internal.application.service.yolo_train import (
        _runtime_train_process_on_gpu,
    )

    deadline = time.monotonic() + max(int(timeout_seconds), 30)
    pending = {int(j) for j in job_ids}
    while pending and time.monotonic() < deadline:
        still: set[int] = set()
        for jid in sorted(pending):
            job = crud_yolo_train_job.get(db=db, job_id=jid)
            if not job:
                continue
            status = str(job.status or "")
            phase, runtime_pid = _read_deploy_train_phase(jid)
            if status == YoloTrainStatus.CANCELLED.value:
                continue
            if phase in ("cancelled", "completed", "failed"):
                continue
            if runtime_pid and _runtime_train_process_on_gpu(runtime_pid):
                still.add(jid)
                continue
            if status in (
                YoloTrainStatus.PENDING.value,
                YoloTrainStatus.PROCESSING.value,
            ) and phase in _DEPLOY_ACTIVE_PHASES:
                still.add(jid)
                continue
        pending = still
        if pending:
            time.sleep(poll_interval)
    if pending:
        logger.warning(
            "deploy pause wait timeout; still active job_ids={}", sorted(pending)
        )
        return False
    return True


def pause_active_jobs_for_deploy(
    db: Session,
    *,
    wait_timeout_seconds: int = 3600,
) -> List[int]:
    """暂停所有进行中的训练并写入续训队列；返回已暂停 job_id 列表。"""
    active = list_active_train_jobs(db)
    if not active:
        return []
    job_ids = [int(j.id) for j in active]
    mark_deploy_pause_in_progress()
    paused: List[int] = []
    for jid in job_ids:
        if pause_train_job_for_deploy(db, jid):
            paused.append(jid)
    if paused:
        save_deploy_resume_queue(paused)
        if not wait_jobs_paused_for_deploy(
            db, paused, timeout_seconds=wait_timeout_seconds
        ):
            raise RuntimeError(
                f"等待训练暂停超时（job_ids={paused}），请检查 GPU 进程后重试 deploy"
            )
    else:
        clear_deploy_pause_in_progress()
    return paused


def _resume_deploy_paused_jobs_locked(*, delay_seconds: float = 2.0) -> int:
    """重启后恢复部署前暂停的训练作业。"""
    if not settings.YOLO_TRAIN_ENABLED:
        clear_deploy_pause_in_progress()
        clear_deploy_resume_queue()
        return 0

    job_ids = load_deploy_resume_queue()
    if not job_ids:
        clear_deploy_pause_in_progress()
        return 0

    from labelu.internal.application.service.yolo_train import (
        _restart_train_job_from_existing_job,
        ensure_ultralytics_installed,
    )

    if not ensure_ultralytics_installed(reason="部署后自动继续训练"):
        logger.warning(
            "deploy resume skipped: ultralytics not installed (job_ids={})",
            job_ids,
        )
        return 0

    if delay_seconds > 0:
        time.sleep(delay_seconds)

    resumed = 0
    failed_ids: List[int] = []
    with session_scope() as db:
        for jid in job_ids:
            try:
                if _restart_train_job_from_existing_job(db, jid):
                    resumed += 1
                    logger.info("deploy resume: restarted job_id={}", jid)
                else:
                    failed_ids.append(jid)
                    logger.warning("deploy resume: failed job_id={}", jid)
            except Exception:
                failed_ids.append(jid)
                logger.exception("deploy resume failed job_id={}", jid)

    if not failed_ids:
        clear_deploy_resume_queue()
        clear_deploy_pause_in_progress()
    else:
        save_deploy_resume_queue(failed_ids)
        logger.warning(
            "deploy resume partial: resumed={}/{}; queue kept for {}",
            resumed,
            len(job_ids),
            failed_ids,
        )
    return resumed


def resume_deploy_paused_jobs(*, delay_seconds: float = 2.0) -> int:
    with deploy_resume_claim_lock() as acquired:
        if not acquired:
            logger.info("deploy resume skipped: another process owns the resume queue")
            return 0
        return _resume_deploy_paused_jobs_locked(delay_seconds=delay_seconds)


def deploy_pause_and_wait_cli(*, wait_timeout_seconds: int = 3600) -> dict[str, Any]:
    if not settings.YOLO_TRAIN_ENABLED:
        return {"paused": [], "skipped": "YOLO_TRAIN_ENABLED=false"}
    with session_scope() as db:
        paused = pause_active_jobs_for_deploy(
            db, wait_timeout_seconds=wait_timeout_seconds
        )
    return {"paused": paused}


def deploy_resume_cli() -> dict[str, Any]:
    resumed = resume_deploy_paused_jobs()
    return {"resumed": resumed, "queue_remaining": load_deploy_resume_queue()}
