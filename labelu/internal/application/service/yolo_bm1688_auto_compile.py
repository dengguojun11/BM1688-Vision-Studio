"""自动把训练完成且生成 best.pt 的 YOLO 作业送入 BM1688 导出/编译。"""

from __future__ import annotations

import threading
from loguru import logger
from labelu.internal.adapter.persistence import (
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_train_job,
)
from labelu.internal.application.service import yolo_bm1688_export as export_service
from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_AUTO_COMPILE_LOCK = threading.Lock()
_AUTO_COMPILE_ACTIVE: set[int] = set()


def _claim(job_id: int) -> bool:
    with _AUTO_COMPILE_LOCK:
        normalized = int(job_id)
        if normalized in _AUTO_COMPILE_ACTIVE:
            return False
        _AUTO_COMPILE_ACTIVE.add(normalized)
        return True


def _release(job_id: int) -> None:
    with _AUTO_COMPILE_LOCK:
        _AUTO_COMPILE_ACTIVE.discard(int(job_id))


def _compiler_ready() -> bool:
    if not bool(settings.YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED):
        return False
    cfg = cfg_io.load_bm1688_compiler_config()
    if not bool(cfg.get("auto_compile", True)):
        return False
    if (cfg.get("custom_compile_command") or "").strip():
        return True
    if (
        settings.YOLO_BM1688_COMPILE_COMMAND or ""
    ).strip() and settings.YOLO_BM1688_COMPILE_ENABLED:
        return True
    root = (cfg.get("tpu_mlir_root") or "").strip()
    script = export_service._builtin_compile_script()
    return bool(root and script.is_file())


def maybe_auto_compile_job(job_id: int, *, trigger: str = "post_train") -> bool:
    """Create and submit one export after a completed job produces ``best.pt``.

    Training quality is evaluated separately and remains a release/publish gate;
    it must not prevent producing a BM1688 artifact for a valid PT model.
    """
    # 自动流程的入口不应被编译器是否已经就绪拦住：先登记并生成 ONNX，
    # 编译器就绪后由导出恢复看门狗继续生成 BModel。
    if not settings.YOLO_TRAIN_ENABLED or not bool(
        settings.YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED
    ):
        return False
    if not _claim(job_id):
        return False
    try:
        export_id = None
        with session_scope() as db:
            with begin_transaction(db):
                job = (
                    db.query(YoloTrainJob)
                    .filter(YoloTrainJob.id == int(job_id))
                    .with_for_update()
                    .first()
                )
                if job is None or job.status != YoloTrainStatus.COMPLETED.value:
                    return False
                if (
                    job.auto_training_round_id is not None
                    and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
                ):
                    return False
                if not (job.weights_path or "").strip():
                    return False
                task = crud_task.get(db=db, task_id=int(job.task_id))
                owner = (
                    crud_user.get(db=db, id=int(job.created_by))
                    if job.created_by
                    else None
                )
                if task is None or owner is None:
                    logger.warning(
                        "skip automatic BM1688 compile job_id={} task_or_owner_missing",
                        job_id,
                    )
                    return False
                existing = crud_yolo_bm1688_export.list_all_for_job(
                    db, yolo_train_job_id=int(job.id)
                )
                if existing:
                    return False
                row, created = export_service.create_export_for_job_system(
                    db,
                    task_id=int(job.task_id),
                    job_id=int(job.id),
                    user_id=int(owner.id),
                )
                if not created:
                    return False
                export_id = int(row.id)
        if export_id is not None:
            export_service.submit_export_background(export_id)
            logger.info(
                "automatic BM1688 compile queued job_id={} export_id={} trigger={}",
                job_id,
                export_id,
                trigger,
            )
            return True
        return False
    except Exception:
        logger.exception(
            "automatic BM1688 compile failed to queue job_id={} trigger={}",
            job_id,
            trigger,
        )
        return False
    finally:
        _release(job_id)


def scan_completed_training_jobs_missing_exports() -> int:
    """Reconcile completed jobs with weights that predate the automatic hook."""
    if not settings.YOLO_TRAIN_ENABLED or not bool(
        settings.YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED
    ):
        return 0
    with session_scope() as db:
        jobs = crud_yolo_train_job.list_completed_with_weights(db)
        job_ids = [int(job.id) for job in jobs]
    created = 0
    batch_size = max(1, int(settings.YOLO_BM1688_AUTO_COMPILE_BATCH_SIZE))
    for job_id in job_ids:
        if created >= batch_size:
            break
        if maybe_auto_compile_job(job_id, trigger="recovery_scan"):
            created += 1
    if created:
        logger.info("automatic BM1688 compile recovery queued {} export(s)", created)
    return created


# Compatibility name retained for existing watchdog imports and deployments.
scan_passed_training_jobs_missing_exports = scan_completed_training_jobs_missing_exports
