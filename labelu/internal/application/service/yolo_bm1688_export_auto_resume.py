"""部署重启后自动恢复 PENDING/PROCESSING 的 BM1688 导出任务。"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import List

from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
from labelu.internal.adapter.persistence import crud_yolo_edge_validation_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.execution_fencing import execution_lease_is_active
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export

_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()
_EDGE_RECOVERY_LOCK = threading.Lock()
_EDGE_RECOVERY_ACTIVE: set[int] = set()


def _statuses_to_scan() -> tuple[str, ...]:
    # ONNX_READY 表示 PT 已自动导出 ONNX，但当时编译环境未就绪；
    # 编译器恢复后由同一看门狗自动续编译。
    return ("PENDING", "PROCESSING", "ONNX_READY")


def _list_stuck_export_ids(db: Session) -> List[int]:
    query = db.query(YoloBm1688Export.id, YoloBm1688Export.status).filter(
        YoloBm1688Export.status.in_(_statuses_to_scan())
    )
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        query = query.filter(YoloBm1688Export.auto_training_round_id.is_(None))
    rows = (
        query
        .order_by(YoloBm1688Export.id.asc())
        .all()
    )
    # 没有编译器时不要每轮重复尝试 ONNX_READY；PENDING/PROCESSING
    # 仍按原有恢复逻辑处理。编译器可用后下一轮会自动接管。
    compiler_ready = False
    try:
        from labelu.internal.application.service.yolo_bm1688_auto_compile import (
            _compiler_ready,
        )

        compiler_ready = _compiler_ready()
    except Exception:
        compiler_ready = False
    return [
        int(r[0])
        for r in rows
        if str(r[1] or "").upper() != "ONNX_READY"
        or compiler_ready
    ]


def _list_completed_exports_missing_edge_jobs(db: Session) -> List[int]:
    """Find completed exports for which the post-export edge job was never created."""
    return crud_yolo_bm1688_export.list_completed_without_edge_validation_job_ids(db)


def _edge_auto_dispatch_enabled() -> bool:
    from labelu.internal.application.service import yolo_edge_validation_scheduler
    from labelu.internal.common import edge_ssh_config as edge_cfg_io

    return bool(
        yolo_edge_validation_scheduler.should_auto_dispatch()
        or edge_cfg_io.should_auto_run_edge_ssh()
    )


def _claim_edge_recovery(export_id: int) -> bool:
    with _EDGE_RECOVERY_LOCK:
        eid = int(export_id)
        if eid in _EDGE_RECOVERY_ACTIVE:
            return False
        _EDGE_RECOVERY_ACTIVE.add(eid)
        return True


def _release_edge_recovery(export_id: int) -> None:
    with _EDGE_RECOVERY_LOCK:
        _EDGE_RECOVERY_ACTIVE.discard(int(export_id))


def _dedicated_bm1688_worker_enabled() -> bool:
    from labelu.internal.application.service.bm1688_worker_runtime import (
        dedicated_worker_enabled,
    )

    return bool(dedicated_worker_enabled())


def maybe_auto_resume_export(export_id: int) -> bool:
    from labelu.internal.application.service.yolo_bm1688_export import (
        _resolve_onnx_path_for_row,
        _run_bm1688_export_sync,
        _run_bm1688_recompile_sync,
        is_export_active,
    )

    eid = int(export_id)
    if is_export_active(eid):
        return False

    has_onnx = False
    status = ""
    recovery_owner: str | None = None
    with session_scope() as db:
        row = crud_yolo_bm1688_export.get(db, eid)
        if not row or row.status not in _statuses_to_scan():
            return False
        if execution_lease_is_active(row, now=datetime.now()):
            logger.info(
                "skip BM1688 export recovery with active execution lease export_id={}",
                eid,
            )
            return False
        if (
            getattr(row, "auto_training_round_id", None) is not None
            and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
        ):
            return False
        status = str(row.status or "")

        # 独立 BM1688 Worker 开启后，Web 进程不能在线程中直接编译。
        # 对已失去执行租约的 PROCESSING 任务只做状态回队，后续由
        # Worker 按 worker_action 领取；PENDING 任务保持原状态等待领取。
        if _dedicated_bm1688_worker_enabled():
            if status == "PROCESSING":
                with begin_transaction(db):
                    current = crud_yolo_bm1688_export.get(db, eid)
                    if (
                        current is not None
                        and str(current.status or "") == "PROCESSING"
                        and not execution_lease_is_active(current, now=datetime.now())
                    ):
                        crud_yolo_bm1688_export.update_row(
                            db,
                            current,
                            status="PENDING",
                            error_message=None,
                        )
            logger.info(
                "skip BM1688 export Web recovery; dedicated Worker owns export_id={} status={}",
                eid,
                status,
            )
            return False
        try:
            _resolve_onnx_path_for_row(row)
            has_onnx = True
        except LabelUException:
            has_onnx = False

        # Automatic recovery must win a database lease before starting a
        # thread. Manual exports retain the legacy in-process recovery path.
        if (
            getattr(row, "auto_training_round_id", None) is not None
            and str(getattr(row, "pipeline_origin", "") or "")
            in {"AUTO_QUALITY", "MANUAL_DIAGNOSTIC"}
        ):
            from labelu.internal.application.service.auto_training_dispatch import (
                claim_bm1688_recovery,
            )

            claimed, recovery_owner = claim_bm1688_recovery(
                db,
                export_id=eid,
                status=status,
                now=datetime.now(),
            )
            if not claimed:
                return False

    def _run() -> None:
        try:
            if has_onnx:
                _run_bm1688_recompile_sync(eid)
            else:
                _run_bm1688_export_sync(eid)
        finally:
            if recovery_owner:
                from labelu.internal.application.service.auto_training_dispatch import (
                    release_bm1688_recovery,
                )

                try:
                    with session_scope() as release_db:
                        release_bm1688_recovery(
                            release_db,
                            export_id=eid,
                            owner=recovery_owner,
                        )
                except Exception:
                    logger.exception(
                        "failed to release BM1688 recovery lease export_id={}", eid
                    )

    try:
        threading.Thread(
            target=_run,
            daemon=True,
            name=f"bm1688-export-resume-{eid}",
        ).start()
    except Exception:
        if recovery_owner:
            from labelu.internal.application.service.auto_training_dispatch import (
                release_bm1688_recovery,
            )

            try:
                with session_scope() as release_db:
                    release_bm1688_recovery(
                        release_db,
                        export_id=eid,
                        owner=recovery_owner,
                    )
            except Exception:
                logger.exception(
                    "failed to release BM1688 recovery lease after thread start failure export_id={}",
                    eid,
                )
        raise
    logger.info(
        "auto-resume bm1688 export {} (status={}, onnx_on_disk={})",
        eid,
        status,
        has_onnx,
    )
    return True


def scan_and_auto_resume_all_exports() -> int:
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_BM1688_AUTO_RESUME_ENABLED:
        return 0

    with session_scope() as db:
        export_ids = _list_stuck_export_ids(db)

    resumed = 0
    for eid in export_ids:
        if maybe_auto_resume_export(eid):
            resumed += 1
    if resumed:
        logger.info("auto-resume bm1688 export: submitted {} export(s)", resumed)
    return resumed


def maybe_auto_dispatch_completed_export(export_id: int) -> bool:
    """Recover the missing post-export edge validation job exactly once per run."""
    if not _edge_auto_dispatch_enabled():
        return False

    from labelu.internal.adapter.persistence import crud_task, crud_user
    from labelu.internal.application.service.yolo_bm1688_export import (
        _maybe_run_edge_ssh_validation_sync,
        is_export_active,
    )

    eid = int(export_id)
    if is_export_active(eid):
        return False

    with session_scope() as db:
        row = crud_yolo_bm1688_export.get(db, eid)
        if not row or str(row.status or "").upper() != "COMPLETED":
            return False
        if (
            getattr(row, "auto_training_round_id", None) is not None
            and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
        ):
            return False
        latest = crud_yolo_edge_validation_job.get_latest_for_export(
            db, export_id=eid
        )
        if latest is not None and str(latest.status or "").upper() in {
            "PENDING",
            "PROCESSING",
            "COMPLETED",
            "PARTIAL_COMPLETED",
        }:
            return False
        bmodel_rel_path = str(row.bmodel_rel_path or "").strip()
        if not bmodel_rel_path:
            logger.warning(
                "edge validation recovery skipped: completed export has no bmodel "
                "export_id={}",
                eid,
            )
            return False
        task = crud_task.get(db=db, task_id=int(row.task_id))
        if task is None:
            logger.warning(
                "edge validation recovery skipped: task missing or deleted "
                "export_id={} task_id={}",
                eid,
                row.task_id,
            )
            return False
        owner = crud_user.get(db=db, id=int(row.user_id))
        if owner is None:
            logger.error(
                "edge validation recovery skipped: export owner missing export_id={} user_id={}",
                eid,
                row.user_id,
            )
            return False
        train_job_id = int(row.yolo_train_job_id)
        imgsz = int(row.imgsz or 640)

    if not _claim_edge_recovery(eid):
        return False

    try:

        def _run() -> None:
            try:
                # Recheck persisted state after the thread is scheduled. This
                # closes the race with the normal post-export callback.
                with session_scope() as check_db:
                    current = crud_yolo_edge_validation_job.get_latest_for_export(
                        check_db, export_id=eid
                    )
                    if current is not None and str(current.status or "").upper() in {
                        "PENDING",
                        "PROCESSING",
                        "COMPLETED",
                        "PARTIAL_COMPLETED",
                    }:
                        return
                created = _maybe_run_edge_ssh_validation_sync(
                    eid,
                    yolo_train_job_id=train_job_id,
                    imgsz=imgsz,
                    force_run=True,
                )
                if created is False:
                    logger.error(
                        "edge validation recovery did not create a job export_id={}",
                        eid,
                    )
            except Exception:
                logger.exception(
                    "edge validation recovery failed export_id={} train_job_id={}",
                    eid,
                    train_job_id,
                )
            finally:
                _release_edge_recovery(eid)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"bm1688-edge-validation-recovery-{eid}",
        ).start()
        logger.warning(
            "auto-dispatch missing edge validation job export_id={} train_job_id={}",
            eid,
            train_job_id,
        )
        return True
    except Exception:
        _release_edge_recovery(eid)
        raise


def scan_and_auto_dispatch_completed_exports() -> int:
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_BM1688_AUTO_RESUME_ENABLED:
        return 0
    if not _edge_auto_dispatch_enabled():
        return 0

    with session_scope() as db:
        export_ids = _list_completed_exports_missing_edge_jobs(db)

    dispatched = sum(
        1 for eid in export_ids if maybe_auto_dispatch_completed_export(eid)
    )
    if dispatched:
        logger.info(
            "auto-dispatch completed BM1688 exports: submitted {} export(s)",
            dispatched,
        )
    return dispatched


def scan_and_auto_compile_passed_training_jobs() -> int:
    """Queue completed PT jobs missing an automatic BM1688 export."""
    if (
        not settings.YOLO_TRAIN_ENABLED
        or not settings.YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED
    ):
        return 0
    from labelu.internal.application.service.yolo_bm1688_auto_compile import (
        scan_passed_training_jobs_missing_exports,
    )

    return scan_passed_training_jobs_missing_exports()


def schedule_bm1688_export_auto_resume_watchdog() -> None:
    """启动时立即扫描一次，之后按间隔周期性恢复中断的导出。"""
    global _WATCHDOG_STARTED
    if not settings.YOLO_TRAIN_ENABLED or not (
        settings.YOLO_BM1688_AUTO_RESUME_ENABLED
        or settings.YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED
    ):
        return
    intervals = []
    if settings.YOLO_BM1688_AUTO_RESUME_ENABLED:
        intervals.append(int(settings.YOLO_BM1688_AUTO_RESUME_SCAN_INTERVAL_SECONDS))
    if settings.YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED:
        intervals.append(int(settings.YOLO_BM1688_AUTO_COMPILE_SCAN_INTERVAL_SECONDS))
    interval = max(30, min(intervals))

    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _tick() -> None:
        try:
            scan_and_auto_resume_all_exports()
            scan_and_auto_dispatch_completed_exports()
            scan_and_auto_compile_passed_training_jobs()
        except Exception:
            logger.exception("bm1688 export auto-resume watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    def _initial_scan() -> None:
        try:
            scan_and_auto_resume_all_exports()
            scan_and_auto_dispatch_completed_exports()
            scan_and_auto_compile_passed_training_jobs()
        except Exception:
            logger.exception("bm1688 export auto-resume initial scan failed")

    threading.Thread(target=_initial_scan, daemon=True).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info(
        "bm1688 export auto-resume watchdog started (interval={}s)", interval
    )
