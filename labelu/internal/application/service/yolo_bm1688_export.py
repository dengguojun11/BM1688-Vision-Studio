"""将训练完成的 YOLO best.pt 导出为 ONNX，并按 sophon-demo YOLOv8_plus_det §3.2 生成 models/ 目录树。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional, Tuple

_ACTIVE_BM1688_EXPORTS: set[int] = set()
_ACTIVE_BM1688_EXPORTS_LOCK = threading.Lock()
_RECOMPILE_WAITERS: set[int] = set()
_RECOMPILE_WAITERS_LOCK = threading.Lock()


@dataclass
class _ExportSummaryFlight:
    """合并同一筛选条件下正在进行的全局摘要计算。

    这是 single-flight，不是结果缓存：计算完成后立即移除，后续请求仍会
    读取最新数据。这样可以避免并发打开模型中心时重复加载全量导出记录。
    """

    event: threading.Event
    result: Any = None
    error: BaseException | None = None


_EXPORT_SUMMARY_FLIGHTS: dict[tuple[Any, ...], _ExportSummaryFlight] = {}
_EXPORT_SUMMARY_FLIGHTS_LOCK = threading.Lock()
_EXPORT_SUMMARY_CHUNK_SIZE = 500


_TPU_MLIR_VERSION_RE = re.compile(r"\bTPU-MLIR\s+v([^\s\\r\\n]+)", re.IGNORECASE)


def _compiler_version_from_log(compile_log: Any) -> Optional[str]:
    """从本次真实编译日志提取 TPU-MLIR 版本，不用默认值冒充工具链证据。"""

    text = str(compile_log or "")
    match = _TPU_MLIR_VERSION_RE.search(text)
    if not match:
        return None
    version = str(match.group(1) or "").strip().rstrip(".,;)")
    return version or None


def _run_export_summary_singleflight(
    key: tuple[Any, ...],
    compute,
):
    with _EXPORT_SUMMARY_FLIGHTS_LOCK:
        flight = _EXPORT_SUMMARY_FLIGHTS.get(key)
        owner = flight is None
        if owner:
            flight = _ExportSummaryFlight(event=threading.Event())
            _EXPORT_SUMMARY_FLIGHTS[key] = flight

    if not owner:
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        return flight.result

    try:
        flight.result = compute()
        return flight.result
    except BaseException as exc:
        flight.error = exc
        raise
    finally:
        with _EXPORT_SUMMARY_FLIGHTS_LOCK:
            if _EXPORT_SUMMARY_FLIGHTS.get(key) is flight:
                _EXPORT_SUMMARY_FLIGHTS.pop(key, None)
        flight.event.set()


from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_model_version,
    crud_strategy_task_integration,
    crud_task,
    crud_yolo_board_validation,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.response.yolo_train import (
    YoloBm1688ExportCatalogSummary,
    YoloBm1688ExportDetailResponse,
    YoloBm1688ExportFamilyItemResponse,
    YoloBm1688ExportFamilyResponse,
    YoloBm1688ExportRowResponse,
)
from labelu.internal.application.response.board_agent import (
    BoardValidationProgressResponse,
    BoardValidationStatusResponse,
)
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service.yolo_train import (
    _assert_task_access,
    _job_work_root,
    resolve_weights_path,
)
from labelu.internal.common.yolo_bm1688_calibration import count_calibration_images
from labelu.internal.common.yolo_bm1688_docker import (
    docker_fpfwd_from_cfg,
    quantize_backend_from_cfg,
    run_docker_int8_quant,
)
from labelu.internal.common.yolo_bm1688_sophon import (
    discover_qtable_path,
    ensure_sigmoid_f16_in_qtable,
    is_reused_yolov11s_qtable,
    onnx_filename_for_stem,
    qtable_filename_for_stem,
    resolve_export_calibration_dir,
    ultralytics_weights_to_sophon_stem,
)
from labelu.internal.common.bm1688_sophon_onnx import export_yolo_to_sophon_onnx
from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.task_access import is_consistent_pool_admin, is_task_superuser
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.project_scope import ProjectScopeError, assert_project_chain
from labelu.internal.common.phase_one_contract import (
    ensure_phase_one_bm1688_platforms,
    ensure_phase_one_training_job_type,
)
from labelu.internal.application.service import (
    bm1688_compiler_config as bm1688_compiler_cfg_service,
)
from labelu.internal.common.optional_dependencies import ensure_ultralytics_installed
from labelu.internal.common.model_asset_manifest import build_export_artifact_manifest
from labelu.internal.common.model_manifest_signature import sign_model_manifest
from labelu.internal.common.bm1688_compile_contract import (
    build_bm1688_profile_snapshot,
    build_onnx_export_contract,
    evaluate_bm1688_compile_contract,
)
from labelu.internal.common.bm1688_quality_rules import get_bm1688_quality_rule
from labelu.internal.common.strategy_task_marker import strategy_task_marker
from labelu.internal.common.execution_fencing import (
    ExecutionFencingLost,
    ExecutionFencingUnavailable,
    ExecutionLeaseMonitor,
    claim_execution_lease,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.algorithm import AlgorithmDefinition
from labelu.internal.domain.models.dataset import DatasetVersion
from labelu.internal.domain.models.model_version import ModelArtifact, ModelVersion
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_board_validation import YoloBoardValidation
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def _maybe_run_edge_ssh_validation_sync(
    export_id: int,
    *,
    yolo_train_job_id: int,
    imgsz: int,
    force_run: bool = False,
    execution_monitor: Optional[ExecutionLeaseMonitor] = None,
) -> bool:
    from labelu.internal.application.service import yolo_edge_validation_scheduler
    from labelu.internal.common import edge_ssh_config as edge_cfg_io

    if not force_run and not (
        yolo_edge_validation_scheduler.should_auto_dispatch()
        or edge_cfg_io.should_auto_run_edge_ssh()
    ):
        return False
    if execution_monitor is not None:
        execution_monitor.ensure_current()
    try:
        from labelu.internal.adapter.persistence import crud_user
        from labelu.internal.application.service import yolo_edge_validation
        from labelu.internal.common.db import session_scope

        with session_scope() as db:
            if execution_monitor is not None:
                execution_monitor.ensure_current()
            export_row = crud_yolo_bm1688_export.get(db, export_id)
            if not export_row:
                return False
            current_user = crud_user.get(db=db, id=int(export_row.user_id))
            if current_user is None:
                logger.warning(
                    "skip unified edge validation auto-run because export owner missing export_id={}",
                    export_id,
                )
                return False
            edge_kwargs = {
                "export_id": export_id,
                "current_user": current_user,
            }
            if execution_monitor is not None:
                edge_kwargs["execution_monitor"] = execution_monitor
            result = yolo_edge_validation.run_legacy_edge_ssh_job_sync(
                db,
                **edge_kwargs,
            )
            if execution_monitor is not None:
                execution_monitor.ensure_current()
            return result is not None
    except ExecutionFencingLost:
        raise
    except Exception:
        logger.exception("edge SSH validation failed export_id={}", export_id)
        return False


BM1688_DOC_HINT = (
    "对齐 sophon-demo YOLOv8_plus_det §3.2；本页展示编译服务器侧质量报告。"
    "§4/§6.2 板端例程请在算能盒执行。"
)
_CATALOG_VALUE_UNSET = object()


def _generate_quality_report_sync(
    export_id: int,
    *,
    run_onnx_val: bool = False,
    execution_monitor: Optional[ExecutionLeaseMonitor] = None,
) -> None:
    from labelu.internal.common.bm1688_quality_report import (
        persist_quality_report_for_export,
    )
    from labelu.internal.common.db import session_scope

    try:
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        with session_scope() as db:
            if execution_monitor is not None:
                execution_monitor.ensure_current()
            persist_quality_report_for_export(
                db,
                export_id,
                run_onnx_val=run_onnx_val,
                execution_monitor=execution_monitor,
            )
    except ExecutionFencingLost:
        raise
    except Exception:
        logger.exception("quality_report failed export_id={}", export_id)


def _export_dir_for_row(export_id: int) -> Path:
    return (
        Path(settings.MEDIA_ROOT) / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    )


def is_export_active(export_id: int) -> bool:
    with _ACTIVE_BM1688_EXPORTS_LOCK:
        return int(export_id) in _ACTIVE_BM1688_EXPORTS


def _invalidate_edge_validation_for_recompile(db: Session, export_id: int) -> None:
    """让旧 BModel 的边端证据立即失效，避免新制品沿用旧验证结果。"""
    now = datetime.now()
    db.query(YoloEdgeValidationJob).filter(
        YoloEdgeValidationJob.export_id == int(export_id),
        YoloEdgeValidationJob.status.in_(
            ("PENDING", "PROCESSING", "COMPLETED", "PARTIAL_COMPLETED")
        ),
    ).update(
        {
            YoloEdgeValidationJob.status: "STALE",
            YoloEdgeValidationJob.error_message: (
                "BM1688 制品已申请重新编译，原边端验证已失效，请重新验证"
            ),
            YoloEdgeValidationJob.updated_at: now,
        },
        synchronize_session=False,
    )
    db.query(YoloBoardValidation).filter(
        YoloBoardValidation.export_id == int(export_id),
        YoloBoardValidation.status.in_(
            ("PENDING", "PROCESSING", "COMPLETED", "PARTIAL_COMPLETED")
        ),
    ).update(
        {
            YoloBoardValidation.status: "STALE",
            YoloBoardValidation.error_message: (
                "BM1688 制品已申请重新编译，原边端验证已失效，请重新验证"
            ),
            YoloBoardValidation.updated_at: now,
        },
        synchronize_session=False,
    )


def _mark_recompile_pending(export_id: int) -> bool:
    """在当前编译释放后把手动请求交给队列。"""
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        row = crud_yolo_bm1688_export.get(db, int(export_id))
        if (
            not row
            or str(getattr(row, "worker_action", "") or "").lower() != "recompile"
        ):
            return False
        with begin_transaction(db):
            current = crud_yolo_bm1688_export.get(db, int(export_id))
            if (
                not current
                or str(getattr(current, "worker_action", "") or "").lower()
                != "recompile"
            ):
                return False
            crud_yolo_bm1688_export.update_row(
                db,
                current,
                status="PENDING",
                queue_lease_owner=None,
                queue_lease_expires_at=None,
                error_message=None,
            )
            # 自动编译收尾阶段可能刚创建过一条新的边端验证，这里再失效
            # 一次，确保即将开始的手动制品不会继承它。
            _invalidate_edge_validation_for_recompile(db, export_id)
        return True


def _wait_and_start_recompile(export_id: int, *, dedicated_worker: bool) -> None:
    """等旧执行完全退出后再启动手动编译，防止覆盖目录并发写入。"""
    eid = int(export_id)
    try:
        from labelu.internal.common.db import session_scope
        from labelu.internal.common.execution_fencing import execution_lease_is_active

        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if is_export_active(eid):
                time.sleep(1.0)
                continue
            with session_scope() as db:
                row = crud_yolo_bm1688_export.get(db, eid)
                if not row:
                    return
                active_lease = execution_lease_is_active(row, now=datetime.now())
            if active_lease:
                time.sleep(1.0)
                continue
            if not _mark_recompile_pending(eid):
                return
            if not dedicated_worker:
                _run_bm1688_recompile_sync(eid)
            return
        logger.warning("manual BM1688 recompile waiter timed out export_id={}", eid)
    except Exception:
        logger.exception("manual BM1688 recompile waiter failed export_id={}", eid)
    finally:
        with _RECOMPILE_WAITERS_LOCK:
            _RECOMPILE_WAITERS.discard(eid)


def _schedule_recompile_after_current(export_id: int) -> None:
    eid = int(export_id)
    with _RECOMPILE_WAITERS_LOCK:
        if eid in _RECOMPILE_WAITERS:
            return
        _RECOMPILE_WAITERS.add(eid)
    threading.Thread(
        target=_wait_and_start_recompile,
        args=(eid,),
        kwargs={"dedicated_worker": dedicated_bm1688_worker_enabled()},
        daemon=True,
        name=f"bm1688-recompile-wait-{eid}",
    ).start()


@dataclass
class _Bm1688ExportExecutionGuard:
    export_id: int
    mysql_connection: Any = None
    mysql_lock_name: Optional[str] = None

    def release(self) -> None:
        if self.mysql_connection is not None:
            try:
                from sqlalchemy import text

                self.mysql_connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": self.mysql_lock_name},
                )
            except Exception:
                logger.exception(
                    "release BM1688 export lock failed export_id={}", self.export_id
                )
            finally:
                self.mysql_connection.close()
                self.mysql_connection = None
        _unmark_export_active(self.export_id)


def _acquire_export_execution_guard(
    export_id: int,
) -> Optional[_Bm1688ExportExecutionGuard]:
    """Prevent one export from rebuilding its calibration directory concurrently."""
    eid = int(export_id)
    with _ACTIVE_BM1688_EXPORTS_LOCK:
        if eid in _ACTIVE_BM1688_EXPORTS:
            return None
        _ACTIVE_BM1688_EXPORTS.add(eid)

    connection = None
    lock_name = f"labelu:bm1688-export:{eid}"
    try:
        from labelu.internal.common.db import engine

        if engine.dialect.name == "mysql":
            from sqlalchemy import text

            connection = engine.connect()
            acquired = connection.execute(
                text("SELECT GET_LOCK(:lock_name, 0)"),
                {"lock_name": lock_name},
            ).scalar()
            if int(acquired or 0) != 1:
                connection.close()
                _unmark_export_active(eid)
                return None
    except Exception:
        if connection is not None:
            connection.close()
        _unmark_export_active(eid)
        logger.exception("acquire BM1688 export lock failed export_id={}", eid)
        return None

    return _Bm1688ExportExecutionGuard(
        export_id=eid,
        mysql_connection=connection,
        mysql_lock_name=lock_name if connection is not None else None,
    )


def _mark_export_active(export_id: int) -> None:
    with _ACTIVE_BM1688_EXPORTS_LOCK:
        _ACTIVE_BM1688_EXPORTS.add(int(export_id))


def _unmark_export_active(export_id: int) -> None:
    with _ACTIVE_BM1688_EXPORTS_LOCK:
        _ACTIVE_BM1688_EXPORTS.discard(int(export_id))


def _start_export_execution_monitor(
    export_id: int, *, label: str
) -> tuple[Optional[ExecutionLeaseMonitor], bool, bool]:
    """Claim the execution lease for specialized BM1688 workers.

    Returns ``(monitor, fencing_unavailable, already_claimed)``.  The last
    two flags keep the migration compatibility path explicit: missing lease
    columns may use the legacy worker, while a live owner must stop without
    touching the export row.
    """
    from labelu.internal.common.db import session_scope

    owner = f"{label}:{os.getpid()}:{threading.get_ident()}:{int(export_id)}"
    claim_row_exists = False
    try:
        with session_scope() as fence_db:
            # 只查询主键，避免旧库缺执行租约字段时先在 ORM 全列查询处失败。
            claim_row_exists = (
                fence_db.query(YoloBm1688Export.id)
                .filter(YoloBm1688Export.id == int(export_id))
                .first()
                is not None
            )
            if not claim_row_exists:
                return None, False, False
            token = claim_execution_lease(
                fence_db,
                YoloBm1688Export,
                int(export_id),
                owner=owner,
                lease_seconds=900,
            )
    except ExecutionFencingUnavailable:
        logger.warning(
            "{} execution fencing columns unavailable; using legacy path export_id={}",
            label,
            export_id,
        )
        return None, True, False

    if token is None:
        # 记录存在但 token 为空，代表另一个 worker 持有有效执行租约。
        return None, False, bool(claim_row_exists)
    monitor = ExecutionLeaseMonitor(
        token,
        session_scope_factory=session_scope,
        label=f"{label}-{int(export_id)}",
    ).start()
    monitor.ensure_current()
    return monitor, False, False


def _stop_export_execution_monitor(
    execution_monitor: Optional[ExecutionLeaseMonitor],
) -> None:
    if execution_monitor is None:
        return
    execution_monitor.stop()
    try:
        from labelu.internal.common.db import session_scope
        from labelu.internal.common.execution_fencing import release_execution_lease

        with session_scope() as db:
            release_execution_lease(db, execution_monitor.token)
    except Exception:
        logger.exception("release specialized BM1688 execution lease failed")


def _persist_export_progress(
    export_id: int,
    compile_log_parts: List[str],
    *,
    status: Optional[str] = None,
    onnx_rel_path: Optional[str] = None,
    error_message: Optional[str] = None,
    execution_monitor: Optional[ExecutionLeaseMonitor] = None,
) -> None:
    """导出/编译进行中增量写入 compile_log，便于详情页轮询展示进度。"""
    from labelu.internal.common.db import session_scope

    log_text = "\n".join(compile_log_parts)[:12000] if compile_log_parts else None
    try:
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            if not row:
                return
            updates: dict[str, Any] = {}
            if log_text is not None:
                updates["compile_log"] = log_text
            if status:
                updates["status"] = status
            if onnx_rel_path is not None:
                updates["onnx_rel_path"] = onnx_rel_path
            if error_message is not None:
                updates["error_message"] = error_message
            if not updates:
                return
            if execution_monitor is not None:
                # 在当前短生命周期 Session 上提交，避免监控器另开连接
                # 与外层读事务同时占用同一导出行。
                execution_monitor.conditional_update(
                    updates, db=db, manage_transaction=True
                )
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(db, row, **updates)
    except (ExecutionFencingLost, ExecutionFencingUnavailable):
        raise
    except Exception:
        logger.exception(
            "persist bm1688 export progress failed export_id={}", export_id
        )


def _flush_compile_log(
    export_id: int,
    compile_log_parts: List[str],
    *,
    status: Optional[str] = None,
    onnx_rel_path: Optional[str] = None,
) -> None:
    if export_id > 0:
        _persist_export_progress(
            export_id,
            compile_log_parts,
            status=status,
            onnx_rel_path=onnx_rel_path,
        )


def _open_bm1688_process_log_writers(export_id: int):
    from labelu.internal.common.pipeline_process_log import (
        ProcessLogWriter,
        bm1688_compile_log_path,
        bm1688_quantize_log_path,
    )

    compile_w = ProcessLogWriter(
        bm1688_compile_log_path(export_id),
        logger_prefix=f"bm1688 compile export_id={export_id}",
        header=f"===== BM1688 compile/export export_id={export_id} =====",
    )
    quantize_w = ProcessLogWriter(
        bm1688_quantize_log_path(export_id),
        logger_prefix=f"bm1688 quantize export_id={export_id}",
        header=f"===== BM1688 INT8 quantize export_id={export_id} =====",
    )
    return compile_w, quantize_w


def _bm1688_log_part(
    parts: List[str],
    line: str,
    *,
    compile_writer: Optional[Any] = None,
    quantize_writer: Optional[Any] = None,
    quantize_line: bool = False,
) -> None:
    parts.append(line)
    if compile_writer is not None:
        compile_writer.write(line)
    if quantize_writer is not None and quantize_line:
        quantize_writer.write(line)


def _bm1688_log_subprocess_output(
    parts: List[str],
    *,
    stdout: str = "",
    stderr: str = "",
    compile_writer: Optional[Any] = None,
    quantize_writer: Optional[Any] = None,
    quantize_block: bool = False,
    db_tail: int = 8000,
) -> None:
    """DB compile_log 仍截断；过程日志文件保留完整输出。"""
    if stdout:
        parts.append(stdout[-db_tail:])
        if compile_writer is not None:
            compile_writer.write_block("subprocess_stdout", stdout)
        if quantize_writer is not None and quantize_block:
            quantize_writer.write_block("subprocess_stdout", stdout)
    if stderr:
        parts.append(stderr[-min(db_tail, 4000) :])
        if compile_writer is not None:
            compile_writer.write_block("subprocess_stderr", stderr)
        if quantize_writer is not None and quantize_block:
            quantize_writer.write_block("subprocess_stderr", stderr)


def _stream_subprocess_line(
    line: str,
    *,
    compile_writer: Optional[Any],
    quantize_writer: Optional[Any],
    quantize_block: bool,
    is_stderr: bool,
) -> None:
    if not line:
        return
    prefix = "stderr: " if is_stderr else ""
    if compile_writer is not None:
        compile_writer.write(f"{prefix}{line}")
    if quantize_writer is not None and quantize_block:
        quantize_writer.write(f"{prefix}{line}")


def _run_compile_shell_command(
    cmd: str,
    *,
    env: dict,
    timeout: int,
    compile_log_parts: List[str],
    compile_writer: Optional[Any] = None,
    quantize_writer: Optional[Any] = None,
    quantize_block: bool = False,
) -> int:
    """执行编译 shell 命令并逐行写入过程日志（避免结束后一次性刷出相同时间戳）。"""
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []

    def _pump(stream, chunks: List[str], *, is_stderr: bool) -> None:
        if stream is None:
            return
        for raw in stream:
            chunks.append(raw)
            for line in raw.splitlines():
                _stream_subprocess_line(
                    line,
                    compile_writer=compile_writer,
                    quantize_writer=quantize_writer,
                    quantize_block=quantize_block,
                    is_stderr=is_stderr,
                )
        stream.close()

    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    out_thread = threading.Thread(
        target=_pump, args=(proc.stdout, stdout_chunks), kwargs={"is_stderr": False}
    )
    err_thread = threading.Thread(
        target=_pump, args=(proc.stderr, stderr_chunks), kwargs={"is_stderr": True}
    )
    out_thread.start()
    err_thread.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        raise
    out_thread.join()
    err_thread.join()
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    if stdout:
        compile_log_parts.append(stdout[-8000:])
    if stderr:
        compile_log_parts.append(stderr[-4000:])
    return int(rc)


def _bmodel_basename(file_cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = file_cfg if file_cfg is not None else cfg_io.load_bm1688_compiler_config()
    b = str(cfg.get("bmodel_basename") or cfg_io.DEFAULT_BMODEL_BASENAME).strip()
    return b or cfg_io.DEFAULT_BMODEL_BASENAME


def _compile_platforms(file_cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = file_cfg if file_cfg is not None else cfg_io.load_bm1688_compiler_config()
    return cfg_io.parse_compile_platforms(cfg.get("compile_platforms"))


def _list_bmodel_names(
    export_id: int, file_cfg: Optional[Dict[str, Any]] = None
) -> List[str]:
    root = _export_dir_for_row(export_id)
    models_root = root / "models"
    if not models_root.is_dir():
        return []
    composite_dir = models_root / "BM1688"
    if composite_dir.is_dir():
        for final_name in (
            "plate_recognition_fp16_1b.bmodel",
            "plate_detection_fp16_1b.bmodel",
        ):
            if (composite_dir / final_name).is_file():
                return [f"BM1688/{final_name}"]
    basename = _bmodel_basename(file_cfg)
    canonical = set(cfg_io.canonical_bmodel_names(basename))
    order = cfg_io.canonical_bmodel_names(basename)
    found: List[str] = []
    for platform in cfg_io.SOPHON_PLATFORM_DIR_NAMES:
        pdir = models_root / platform
        if not pdir.is_dir():
            continue
        for name in order:
            if name in canonical and (pdir / name).is_file():
                found.append(f"{platform}/{name}")
    return found


def _pick_primary_bmodel_name(
    names: List[str], file_cfg: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    if not names:
        return None
    for preferred_name in (
        "plate_recognition_fp16_1b.bmodel",
        "plate_detection_fp16_1b.bmodel",
    ):
        for name in names:
            if Path(name).name == preferred_name:
                return name
    prefer_plat = cfg_io.chip_to_platform_dir(_compile_platforms(file_cfg)[0])
    prefer = f"{prefer_plat}/{_bmodel_basename(file_cfg)}_fp16_1b.bmodel"
    if prefer in names:
        return prefer
    for n in names:
        if n.endswith("_fp16_1b.bmodel"):
            return n
    return names[0]


def _persist_artifact_manifest(
    db: Session,
    row,
    *,
    execution_monitor: Optional[ExecutionLeaseMonitor] = None,
) -> None:
    job = crud_yolo_train_job.get(db, row.yolo_train_job_id)
    if not job:
        return
    export_root = _export_dir_for_row(int(row.id))
    source_dataset_manifest = {}
    try:
        source_dataset_manifest = json.loads(job.dataset_manifest_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        source_dataset_manifest = {}
    file_cfg = cfg_io.load_bm1688_compiler_config()
    manifest_compiler_config = dict(file_cfg)
    # 记录导出实际使用的参数，避免仅保存配置文件却无法复核 ONNX 输入。
    manifest_compiler_config.update(
        build_onnx_export_contract(
            sophon_export=bool(file_cfg.get("sophon_export", True)),
            imgsz=int(job.imgsz or 640),
        )
    )
    # 编译配置文件通常只保存路径和参数，真正的 TPU-MLIR 版本只会出现在
    # 本次编译输出中。将日志中的真实版本写入清单，发布门禁才能确认这份
    # BModel 确实由可追溯的工具链生成；提取不到时保持缺失并继续阻断。
    compiler_version = _compiler_version_from_log(getattr(row, "compile_log", None))
    if compiler_version:
        manifest_compiler_config["compiler_version"] = compiler_version
    # Persist the exact conversion/edge threshold snapshot with the export;
    # later workers must never reconstruct a rule from current defaults.
    manifest_compiler_config["quality_rule"] = get_bm1688_quality_rule()
    model_stem = ultralytics_weights_to_sophon_stem(job.model_weights)
    cali_dir, _ = resolve_export_calibration_dir(
        export_root=export_root,
        train_job_work_root=_job_work_root(int(row.yolo_train_job_id)),
        file_cfg=file_cfg,
    )
    qtable_path = export_root / "models" / "onnx" / qtable_filename_for_stem(model_stem)
    if not qtable_path.is_file():
        configured_qtable = str(file_cfg.get("quantize_table_path") or "").strip()
        qtable_path = Path(configured_qtable) if configured_qtable else qtable_path
    bm1688_profile = build_bm1688_profile_snapshot(
        model_family=str(getattr(job, "model_family", None) or "yolov11"),
        model_stem=model_stem,
        imgsz=int(job.imgsz or 640),
        compiler_config=manifest_compiler_config,
        class_names=yolo_train_service._job_contract_response_fields(job)[
            "class_names"
        ],
        calibration_dir=cali_dir,
        qtable_path=qtable_path,
        model_weights_sha256=None,
        model_weights_path=resolve_weights_path(job.weights_path),
    )
    onnx_path = (
        Path(settings.MEDIA_ROOT).resolve() / str(row.onnx_rel_path).replace("\\", "/")
        if row.onnx_rel_path
        else None
    )
    bmodel_paths = [
        export_root / "models" / relative_name
        for relative_name in _list_bmodel_names(int(row.id))
    ]
    # 先生成一份未写入 compile_contract_status 的候选清单，取得本次实际
    # execution_signature。编译门禁需要同时校验 Profile 和清单签名；此前
    # 只传入 Profile，导致每次编译都把 artifact_manifest_signature_missing
    # 错误写入最终清单。
    manifest_candidate = build_export_artifact_manifest(
        export_root,
        train_job_id=int(row.yolo_train_job_id),
        export_id=int(row.id),
        source_dataset_manifest=source_dataset_manifest,
        pt_path=resolve_weights_path(job.weights_path),
        onnx_path=onnx_path,
        bmodel_paths=bmodel_paths,
        calibration_dir=cali_dir,
        compiler_config=manifest_compiler_config,
        compile_log=row.compile_log,
        status=str(row.status or ""),
        class_names=yolo_train_service._job_contract_response_fields(job)[
            "class_names"
        ],
        label_set_signature=getattr(job, "label_set_signature", None),
        class_order_signature=getattr(job, "class_order_signature", None),
        data_yaml_sha256=getattr(job, "data_yaml_sha256", None),
        dataset_content_signature=getattr(job, "dataset_content_signature", None),
        bm1688_profile=bm1688_profile,
    )
    compile_contract = evaluate_bm1688_compile_contract(
        export_root,
        model_stem=model_stem,
        mode=str(file_cfg.get("quantize") or "full"),
        platforms=_compile_platforms(),
        onnx_path=onnx_path,
        calibration_dir=cali_dir,
        compiler_config=manifest_compiler_config,
        artifact_manifest=manifest_candidate,
        bm1688_profile=bm1688_profile,
        fixed_eval_evidence=None,
    )
    # 固定评估集在后续边端验证阶段才产生；这里先把未满足的原因写入
    # 不可变制品清单，发布门禁据此阻断，而不是把导出完成误判为可发布。
    manifest_compiler_config["compile_contract_status"] = compile_contract["status"]
    manifest_compiler_config["compile_contract_reasons"] = compile_contract["reasons"]
    manifest = build_export_artifact_manifest(
        export_root,
        train_job_id=int(row.yolo_train_job_id),
        export_id=int(row.id),
        source_dataset_manifest=source_dataset_manifest,
        pt_path=resolve_weights_path(job.weights_path),
        onnx_path=onnx_path,
        bmodel_paths=bmodel_paths,
        calibration_dir=cali_dir,
        compiler_config=manifest_compiler_config,
        compile_log=row.compile_log,
        status=str(row.status or ""),
        class_names=yolo_train_service._job_contract_response_fields(job)[
            "class_names"
        ],
        label_set_signature=getattr(job, "label_set_signature", None),
        class_order_signature=getattr(job, "class_order_signature", None),
        data_yaml_sha256=getattr(job, "data_yaml_sha256", None),
        dataset_content_signature=getattr(job, "dataset_content_signature", None),
        bm1688_profile=bm1688_profile,
    )
    manifest_secret = str(
        getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "") or ""
    ).strip()
    manifest_required = bool(
        getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_REQUIRED", False)
        or str(getattr(settings, "APP_ENV", "") or "").strip().lower() == "production"
    )
    if manifest_secret:
        manifest = sign_model_manifest(
            manifest,
            secret=manifest_secret,
            key_id=str(
                getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_KEY_ID", "v1") or "v1"
            ),
        )
    elif manifest_required:
        # Preserve the export evidence for operators, but make the missing
        # production key explicit so publication fails closed later.
        manifest["manifest_signature_status"] = "MISSING_KEY"
    artifact_manifest_json = json.dumps(manifest, ensure_ascii=False)
    if execution_monitor is not None:
        execution_monitor.conditional_update(
            {"artifact_manifest_json": artifact_manifest_json},
            db=db,
            manage_transaction=True,
        )
    else:
        crud_yolo_bm1688_export.update_row(
            db,
            row,
            artifact_manifest_json=artifact_manifest_json,
        )


def _sync_model_version_artifacts_for_export(db: Session, row) -> int:
    """Expose completed export artifacts on the matching model version.

    The BM1688 export table tracks compilation, while the model center reads
    ``ModelVersion.artifacts``.  Keep the two records linked by the training
    job so a successful compile is immediately visible without a second
    manual registration step.
    """
    job_id = int(getattr(row, "yolo_train_job_id", 0) or 0)
    if not job_id:
        return 0
    model = crud_model_version.get_by_training_job(db, job_id)
    if model is None or str(getattr(model, "status", "")).upper() == "PUBLISHED":
        return 0

    media_root = Path(settings.MEDIA_ROOT).resolve()
    export_root = _export_dir_for_row(int(row.id))
    candidates: list[tuple[Path, str, str | None]] = []
    if row.onnx_rel_path:
        onnx_path = (media_root / str(row.onnx_rel_path).replace("\\", "/")).resolve()
        candidates.append((onnx_path, "onnx", None))
    for relative_name in _list_bmodel_names(int(row.id)):
        bmodel_path = (export_root / "models" / relative_name).resolve()
        precision = None
        lower_name = bmodel_path.name.lower()
        for marker, value in (
            ("_fp32_", "FP32"),
            ("_fp16_", "FP16"),
            ("_int8_", "INT8"),
        ):
            if marker in lower_name:
                precision = value
                break
        candidates.append((bmodel_path, "bmodel", precision))

    created = 0
    owner_id = int(getattr(model, "created_by", 0) or 0)
    with begin_transaction(db):
        for path, artifact_type, precision in candidates:
            if not path.is_file():
                continue
            try:
                relative_path = path.relative_to(media_root).as_posix()
            except ValueError:
                logger.warning("skip model artifact outside media root: {}", path)
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha256 = digest.hexdigest()
            existing = next(
                (
                    item
                    for item in (model.artifacts or [])
                    if str(item.relative_path or "") == relative_path
                ),
                None,
            )
            if existing is not None:
                # 重新编译会覆盖同一相对路径；同步新的哈希和大小，避免
                # 模型中心继续展示旧 BModel 制品证据。
                existing.sha256 = sha256
                existing.size_bytes = int(path.stat().st_size)
                existing.status = "READY"
                existing.metadata_json = json.dumps(
                    {
                        "source": "yolo_bm1688_export",
                        "export_id": int(row.id),
                        "training_job_id": job_id,
                        "recompiled_at": datetime.now().isoformat(),
                    },
                    ensure_ascii=False,
                )
                db.add(existing)
                continue
            crud_model_version.create_artifact(
                db,
                ModelArtifact(
                    model_version_id=int(model.id),
                    artifact_type=artifact_type,
                    relative_path=relative_path,
                    sha256=sha256,
                    size_bytes=int(path.stat().st_size),
                    chip_type="bm1688" if artifact_type == "bmodel" else None,
                    precision=precision,
                    status="READY",
                    metadata_json=json.dumps(
                        {
                            "source": "yolo_bm1688_export",
                            "export_id": int(row.id),
                            "training_job_id": job_id,
                        },
                        ensure_ascii=False,
                    ),
                    created_by=owner_id,
                ),
            )
            created += 1
    if created:
        logger.info(
            "synced {} BM1688 export artifacts to model_version_id={} export_id={}",
            created,
            model.id,
            row.id,
        )
    return created


def _platform_has_full_set(
    export_root: Path, chip: str, *, model_stem: Optional[str] = None
) -> bool:
    pdir = cfg_io.sophon_platform_dir(export_root, chip)
    stem = (
        str(model_stem or _bmodel_basename()).strip() or cfg_io.DEFAULT_BMODEL_BASENAME
    )
    return all((pdir / n).is_file() for n in cfg_io.canonical_bmodel_names(stem))


def _validation_gate_label_for_job(db: Session, job_id: int) -> str:
    from labelu.internal.application.service.yolo_train import (
        _training_val_metrics_for_job,
        _validation_pass_row,
    )

    job = crud_yolo_train_job.get(db, job_id)
    if not job:
        return "—"
    vp = _validation_pass_row(_training_val_metrics_for_job(job))
    return str(vp.status or "—").strip() or "—"


def _edge_validation_label(board_validation) -> str:
    if board_validation is None:
        return "未开始"
    st = str(getattr(board_validation, "status", "") or "").upper()
    if st == "COMPLETED":
        return "已完成"
    if st == "PARTIAL_COMPLETED":
        return "部分完成"
    if st == "FAILED":
        return "失败"
    if st in {"PROCESSING", "RUNNING"}:
        return "验证中"
    if st == "PENDING":
        return "待验证"
    return st or "—"


def _edge_execution_label(board_validation) -> str:
    if board_validation is None:
        return "未开始"
    st = str(getattr(board_validation, "status", "") or "").upper()
    if st == "COMPLETED":
        report = getattr(board_validation, "report", None)
        if isinstance(report, dict):
            result = report.get("result")
            if isinstance(result, dict):
                eval_summary = result.get("eval_summary")
                execution_summary = result.get("execution_summary")
                if isinstance(eval_summary, dict) and eval_summary:
                    return "结果已回传"
                if isinstance(execution_summary, dict) and execution_summary.get(
                    "has_bmodel_eval"
                ):
                    return "结果已回传"
        return "执行完成"
    if st == "FAILED":
        return "执行失败"
    if st == "PARTIAL_COMPLETED":
        return "部分完成"
    if st in {"PROCESSING", "RUNNING"}:
        return "执行中"
    if st == "PENDING":
        return "待执行"
    return st or "—"


def _quality_report_sop_eval(row, board_validation=None) -> dict:
    from labelu.internal.common.bm1688_quality_report import (
        merge_manual_edge_validation_into_report,
        parse_quality_report,
    )

    raw = getattr(row, "quality_report", None)
    if isinstance(raw, dict):
        report = raw
    else:
        report = parse_quality_report(raw)
    report = merge_manual_edge_validation_into_report(report, board_validation)
    if not isinstance(report, dict):
        return {}
    sop = report.get("sop_eval")
    return sop if isinstance(sop, dict) else {}


def _manual_edge_eval_result(board_validation) -> dict:
    if board_validation is None:
        return {}
    report = getattr(board_validation, "report", None)
    if not isinstance(report, dict):
        return {}
    result = report.get("result")
    if isinstance(result, dict):
        return result
    execution = report.get("execution")
    if isinstance(execution, dict):
        return {"execution": execution}
    return {}


def _manual_edge_bmodel_metrics(board_validation) -> dict:
    result = _manual_edge_eval_result(board_validation)
    eval_summary = result.get("eval_summary")
    if isinstance(eval_summary, dict):
        metrics = {
            key: eval_summary.get(key)
            for key in ("map50", "map50_95", "precision", "recall", "images")
            if eval_summary.get(key) is not None
        }
        if metrics:
            return metrics
    execution = result.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    ingest = execution.get("ingest")
    if not isinstance(ingest, dict):
        ingest = {}
    bmodel_sop = ingest.get("bmodel_sop")
    if not isinstance(bmodel_sop, dict):
        return {}
    metrics = bmodel_sop.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _plate_recognition_edge_eval(row, board_validation) -> dict:
    if board_validation is None:
        return {}
    sop = _quality_report_sop_eval(row, board_validation)
    plate_eval = sop.get("plate_recognition_eval") if isinstance(sop, dict) else None
    if isinstance(plate_eval, dict):
        return plate_eval

    result = _manual_edge_eval_result(board_validation)
    direct = result.get("plate_recognition_eval")
    if isinstance(direct, dict):
        return direct
    eval_models = result.get("eval_models")
    if not isinstance(eval_models, dict):
        return {}
    for item in eval_models.values():
        if not isinstance(item, dict):
            continue
        if str(item.get("backend") or "") != "plate_recognition_bm1688":
            continue
        status = str(item.get("status") or "").strip().lower()
        return {
            "status": status or "unknown",
            "model_type": "plate_recognition",
            "model_tag": item.get("model_tag") or "fp16",
            "model_filename": item.get("model_filename"),
            "backend": "plate_recognition_bm1688",
            "runtime_ok": status == "ok",
        }
    return {}


def _plate_detection_edge_eval(board_validation) -> dict:
    """Return the current plate-detection runtime result, if one was reported."""
    result = _manual_edge_eval_result(board_validation)
    eval_models = result.get("eval_models")
    if not isinstance(eval_models, dict):
        eval_summary = result.get("eval_summary")
        eval_models = (
            eval_summary.get("models") if isinstance(eval_summary, dict) else None
        )
    if not isinstance(eval_models, dict):
        return {}
    for tag in ("int8", "fp16", "fp32"):
        item = eval_models.get(tag)
        if not isinstance(item, dict):
            continue
        backend = str(item.get("backend") or "").strip().lower()
        model_type = str(item.get("model_type") or "").strip().lower()
        if backend != "plate_detection_bm1688" and model_type != "plate_detection":
            continue
        return item
    return {}


def _plate_detection_runtime_status(board_validation) -> str:
    plate_eval = _plate_detection_edge_eval(board_validation)
    if not plate_eval:
        return ""
    status = str(plate_eval.get("status") or "").strip().lower()
    if plate_eval.get("runtime_ok") is False or status in {"fail", "failed", "error"}:
        return "fail"
    input_count = plate_eval.get("input_image_count")
    result_count = plate_eval.get("result_image_count")
    if (
        plate_eval.get("runtime_ok") is True
        and status in {"ok", "pass", "passed"}
        and input_count is not None
        and result_count is not None
        and int(input_count) > 0
        and int(result_count) == int(input_count)
    ):
        return "pass"
    return "review"


def _plate_compatibility_summary(
    row,
    board_validation,
    artifact_manifest: dict | None,
    *,
    training_recipe_key: str | None,
    bmodel_names: list[str],
) -> dict | None:
    manifest = artifact_manifest if isinstance(artifact_manifest, dict) else {}
    manifest_version = str(manifest.get("manifest_version") or "").strip()
    job_type = str(manifest.get("job_type") or "").strip()
    if manifest_version not in {
        "plate_detection_v1",
        "plate_recognition_v1",
    } and job_type not in {
        "plate_detection",
        "plate_recognition",
    }:
        return None

    is_detection = (
        job_type == "plate_detection" or manifest_version == "plate_detection_v1"
    )
    network_names = [
        str(value)
        for value in (manifest.get("network_names") or [])
        if str(value).strip()
    ]
    precision_variants = []
    for name in bmodel_names:
        lower = str(name).lower()
        for precision in ("fp32", "fp16", "int8"):
            if precision in lower and precision not in precision_variants:
                precision_variants.append(precision)

    result = _manual_edge_eval_result(board_validation)
    execution = (
        result.get("execution") if isinstance(result.get("execution"), dict) else {}
    )
    runner_summary = (
        execution.get("runner_summary")
        if isinstance(execution.get("runner_summary"), dict)
        else {}
    )
    generic_check = (
        (runner_summary.get("checks") or {}).get("generic_sophon_demo")
        if isinstance(runner_summary.get("checks"), dict)
        else {}
    )
    generic_status = str((generic_check or {}).get("status") or "").strip().lower()
    if not generic_status and board_validation is not None:
        generic_status = "not_applicable"

    if is_detection:
        runtime_eval = _plate_detection_edge_eval(board_validation)
        runtime_status = (
            _plate_detection_runtime_status(board_validation) or "not_started"
        )
        scope_key = "plate_detection_component"
        scope_label = "车牌检测 Pose 组件"
        compiled_components = ["pose"]
        # A detection-only export is a complete artifact for its contract.
        # Do not compare it with the historical four-component recognition
        # pipeline merely because a legacy job still carries that recipe key.
        expected_components = ["pose"]
        missing_components = []
        if runtime_status == "pass":
            verdict = "component_compatible"
            verdict_label = "车牌检测组件可在 BM1688 运行"
        elif runtime_status == "fail":
            verdict = "incompatible"
            verdict_label = "车牌检测组件板端运行失败"
        else:
            verdict = "review_required"
            verdict_label = "车牌检测组件需要继续验证"
    else:
        runtime_eval = _plate_recognition_edge_eval(row, board_validation)
        runtime_ok = runtime_eval.get("runtime_ok")
        runtime_status = (
            "pass"
            if runtime_ok is True
            else ("fail" if runtime_ok is False else "not_started")
        )
        scope_key = "plate_recognition_composite"
        scope_label = "车牌复合识别模型"
        compiled_components = ["pose", "ocr", "color", "plate_type"]
        expected_components = list(compiled_components)
        missing_components = []
        if runtime_status == "pass":
            verdict = "runtime_compatible_review_required"
            verdict_label = "复合识别运行通过，精度需要复核"
        elif runtime_status == "fail":
            verdict = "incompatible"
            verdict_label = "复合识别板端运行失败"
        else:
            verdict = "review_required"
            verdict_label = "复合识别需要继续验证"

    bmrt = runtime_eval.get("bmrt_test") if isinstance(runtime_eval, dict) else {}
    inference = runtime_eval.get("inference") if isinstance(runtime_eval, dict) else {}
    compile_status = (
        "pass" if str(row.status or "").upper() == "COMPLETED" else "pending"
    )
    return {
        "schema_version": "plate_bm1688_compatibility_v1",
        "model_scope": scope_key,
        "model_scope_label": scope_label,
        "training_recipe_key": training_recipe_key,
        "network_names": network_names,
        "precision_variants": precision_variants,
        "compiled_components": compiled_components,
        "expected_components": expected_components,
        "missing_components": missing_components,
        "compile_status": compile_status,
        "generic_validation_status": generic_status or "not_started",
        "specialized_runtime_status": runtime_status,
        "bmrt_test": bmrt if isinstance(bmrt, dict) else {},
        "inference": inference if isinstance(inference, dict) else {},
        "input_image_count": runtime_eval.get("input_image_count"),
        "result_image_count": runtime_eval.get("result_image_count"),
        "images_with_detections": runtime_eval.get("images_with_detections"),
        "total_detections": runtime_eval.get("total_detections"),
        "precision_evaluation": {
            "status": (
                "computed"
                if runtime_eval.get("map50") is not None
                else str(runtime_eval.get("precision_eval_status") or "not_computed")
            ),
            "type": runtime_eval.get("precision_eval_type") or "bbox",
            "map50": runtime_eval.get("map50"),
            "map50_95": runtime_eval.get("map50_95"),
            "precision": runtime_eval.get("precision"),
            "recall": runtime_eval.get("recall"),
            "f1": runtime_eval.get("f1"),
            "images": runtime_eval.get("images"),
            "reason": runtime_eval.get("precision_eval_reason"),
        },
        "verdict": verdict,
        "verdict_label": verdict_label,
        "release_scope_label": (
            "车牌框与四角点定位运行链路（不包含号码、颜色或类型识别）"
            if is_detection
            else "完整复合识别运行链路"
        ),
    }


def _effective_pt_bmodel_comparison(row, board_validation) -> dict:
    board_status = str(getattr(board_validation, "status", "") or "").upper()
    if board_status != "COMPLETED":
        return {}
    sop = _quality_report_sop_eval(row, board_validation)
    cmp_bm = (sop.get("comparison_pt_bmodel") or {}) if sop else {}
    status = str(cmp_bm.get("status", "") or "").lower()
    if cmp_bm.get("criteria_version") and cmp_bm.get("verdict"):
        return cmp_bm
    manual_metrics = _manual_edge_bmodel_metrics(board_validation)
    if not manual_metrics and sop:
        sop_bmodel_metrics = (sop.get("bmodel") or {}).get("metrics") or {}
        if isinstance(sop_bmodel_metrics, dict):
            manual_metrics = sop_bmodel_metrics
    pt_metrics = (sop.get("pt") or {}).get("metrics") if sop else {}
    if not isinstance(pt_metrics, dict) or pt_metrics.get("map50") is None:
        raw_qr = getattr(row, "quality_report", None)
        if isinstance(raw_qr, dict):
            train_val = raw_qr.get("train_val") or {}
        else:
            from labelu.internal.common.bm1688_quality_report import (
                parse_quality_report,
            )

            parsed = parse_quality_report(raw_qr) if raw_qr else {}
            train_val = (parsed or {}).get("train_val") or {}
        if (
            isinstance(train_val, dict)
            and str(train_val.get("status") or "").lower() == "ok"
            and train_val.get("map50") is not None
        ):
            pt_metrics = {
                key: train_val.get(key)
                for key in ("map50", "map50_95", "precision", "recall", "images")
                if train_val.get(key) is not None
            }
    if not isinstance(manual_metrics, dict) or not manual_metrics:
        return cmp_bm if isinstance(cmp_bm, dict) else {}
    if not isinstance(pt_metrics, dict) or not pt_metrics:
        return cmp_bm if isinstance(cmp_bm, dict) else {}
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        _compare_pt_bmodel,
    )

    return _compare_pt_bmodel(pt_metrics, manual_metrics)


def _edge_int8_eval_label(row, board_validation) -> str:
    if str(getattr(row, "status", "") or "").upper() != "COMPLETED":
        return "待编译"
    if board_validation is None:
        return "待执行"
    bv_status = str(getattr(board_validation, "status", "") or "").upper()
    if bv_status in {"PENDING", "PROCESSING", "RUNNING"}:
        return "评估中"
    if bv_status == "FAILED":
        return "不可评估"
    if bv_status == "PARTIAL_COMPLETED":
        return "证据不完整"
    if bv_status != "COMPLETED":
        return "待执行"
    plate_eval = _plate_recognition_edge_eval(row, board_validation)
    if plate_eval:
        return "需复核" if plate_eval.get("runtime_ok") is True else "不可评估"
    plate_detection_status = _plate_detection_runtime_status(board_validation)
    if plate_detection_status == "pass":
        cmp_bm = _effective_pt_bmodel_comparison(row, board_validation)
        precision_status = str(cmp_bm.get("status", "") or "").lower()
        if precision_status == "ok":
            return "通过"
        if precision_status == "fail":
            return "未通过"
        return "需复核"
    if plate_detection_status == "review":
        return "需复核"
    if plate_detection_status == "fail":
        return "不可评估"
    cmp_bm = _effective_pt_bmodel_comparison(row, board_validation)
    status = str(cmp_bm.get("status", "") or "").lower()
    if status == "ok":
        return "通过"
    if status == "warn":
        return "需复核"
    if status == "fail":
        return "未通过"
    return "待回传"


def _edge_result_status(row, board_validation) -> str:
    if str(getattr(row, "status", "") or "").upper() != "COMPLETED":
        return "not_started"
    if board_validation is None:
        return "not_started"
    bv_status = str(getattr(board_validation, "status", "") or "").upper()
    if bv_status in {"PENDING", "PROCESSING", "RUNNING"}:
        return "running"
    if bv_status == "FAILED":
        return "fail"
    if bv_status == "PARTIAL_COMPLETED":
        return "result_pending"
    if bv_status != "COMPLETED":
        return "unknown"
    plate_eval = _plate_recognition_edge_eval(row, board_validation)
    if plate_eval:
        return "review" if plate_eval.get("runtime_ok") is True else "fail"
    plate_detection_status = _plate_detection_runtime_status(board_validation)
    if plate_detection_status == "fail":
        return "fail"
    if plate_detection_status == "review":
        return "review"
    if plate_detection_status == "pass":
        cmp_bm = _effective_pt_bmodel_comparison(row, board_validation)
        precision_status = str(cmp_bm.get("status", "") or "").lower()
        if precision_status == "ok":
            return "pass"
        if precision_status == "fail":
            return "fail"
        return "review"
    cmp_bm = _effective_pt_bmodel_comparison(row, board_validation)
    status = str(cmp_bm.get("status", "") or "").lower()
    if status == "ok":
        return "pass"
    if status == "warn":
        return "review"
    if status == "fail":
        return "fail"
    cached = (
        str(getattr(board_validation, "edge_result_status", "") or "").strip().lower()
    )
    if cached in {"fail", "review", "result_pending"}:
        return cached
    return "result_pending"


def _verdict_code(row, validation_gate_label: str, board_validation) -> str:
    export_status = str(getattr(row, "status", "") or "").upper()
    if validation_gate_label and validation_gate_label not in {
        "\u8fbe\u6807",
        "\u901a\u8fc7",
    }:
        return "training_failed"
    if export_status in {"PENDING", "PROCESSING"}:
        return "compiling"
    if export_status not in {"COMPLETED", "ONNX_READY"}:
        return "review_required"

    edge_result = _edge_result_status(row, board_validation)
    if edge_result == "pass":
        return "release_ready"
    if edge_result == "fail":
        exec_label = _edge_execution_label(board_validation)
        if exec_label == "\u6267\u884c\u5931\u8d25":
            return "edge_failed"
        return "int8_failed"
    if edge_result in {"not_started", "running", "result_pending"}:
        return "edge_pending"
    return "review_required"


def _verdict_label_from_code(verdict_code: str) -> str:
    mapping = {
        "training_failed": "\u8bad\u7ec3\u672a\u8fbe\u6807",
        "compiling": "\u7f16\u8bd1\u4e2d",
        "edge_pending": "\u5f85\u5b8c\u6210\u8fb9\u7aef\u9a8c\u8bc1",
        "edge_failed": "\u8fb9\u7aef\u6267\u884c\u5931\u8d25",
        "int8_failed": "\u8fb9\u7aef\u7cbe\u5ea6\u672a\u901a\u8fc7",
        "release_ready": "\u53ef\u8fdb\u5165\u53d1\u5e03\u8bc4\u4f30",
        "review_required": "\u5f85\u8fdb\u4e00\u6b65\u786e\u8ba4",
    }
    return mapping.get(
        str(verdict_code or "").strip(), "\u5f85\u8fdb\u4e00\u6b65\u786e\u8ba4"
    )


def _workflow_stage(row, board_validation, validation_gate_label: str = "") -> str:
    export_status = str(getattr(row, "status", "") or "").upper()
    if export_status in {"PENDING", "PROCESSING"}:
        return "compile"
    if export_status not in {"COMPLETED", "ONNX_READY"}:
        return "compile"
    if _verdict_code(row, validation_gate_label, board_validation) == "release_ready":
        return "publish_ready"
    return "edge_validation"


def _current_verdict_label(row, validation_gate_label: str, board_validation) -> str:
    export_status = str(getattr(row, "status", "") or "").upper()
    if validation_gate_label and validation_gate_label not in {
        "\u8fbe\u6807",
        "\u901a\u8fc7",
    }:
        return "\u8bad\u7ec3\u672a\u8fbe\u6807"
    if export_status in {"PENDING", "PROCESSING"}:
        return "\u7f16\u8bd1\u4e2d"
    if export_status not in {"COMPLETED", "ONNX_READY"}:
        return "\u5f85\u7f16\u8bd1"
    int8_label = _edge_int8_eval_label(row, board_validation)
    if int8_label == "\u901a\u8fc7":
        return "\u53ef\u8fdb\u5165\u53d1\u5e03\u8bc4\u4f30"
    if int8_label == "\u672a\u901a\u8fc7":
        return "\u8fb9\u7aef\u7cbe\u5ea6\u672a\u901a\u8fc7"
    if int8_label == "需复核":
        return "边端精度需复核"
    exec_label = _edge_execution_label(board_validation)
    if exec_label in {"\u6267\u884c\u4e2d", "\u5f85\u6267\u884c", "\u672a\u5f00\u59cb"}:
        return "\u5f85\u5b8c\u6210\u8fb9\u7aef\u9a8c\u8bc1"
    if exec_label == "\u7ed3\u679c\u5df2\u56de\u4f20":
        return "\u5f85\u8fdb\u4e00\u6b65\u786e\u8ba4"
    if exec_label == "\u6267\u884c\u5931\u8d25":
        return "\u8fb9\u7aef\u6267\u884c\u5931\u8d25"
    return "\u5f85\u8fdb\u4e00\u6b65\u786e\u8ba4"


def _compact_artifact_manifest(payload: Any) -> Optional[Dict[str, Any]]:
    """Keep lineage evidence while omitting per-file inventories from UI payloads."""
    if not isinstance(payload, dict):
        return None
    compact = dict(payload)
    for key in ("source_dataset", "calibration"):
        block = compact.get(key)
        if not isinstance(block, dict):
            continue
        block = dict(block)
        files = block.pop("files", None)
        if isinstance(files, list):
            block.setdefault("file_count", len(files))
        compact[key] = block
    return compact


def _export_row_to_response(
    row,
    task_name: Optional[str],
    db: Session | None = None,
    *,
    include_board_report: bool = False,
    current_user: User | None = None,
    edge_validation_override: Any = _CATALOG_VALUE_UNSET,
    job_override: Any = _CATALOG_VALUE_UNSET,
    task_override: Any = _CATALOG_VALUE_UNSET,
    quality_policy_snapshot_override: Any = _CATALOG_VALUE_UNSET,
    current_user_is_superuser: Optional[bool] = None,
    strategy_task_override: Any = _CATALOG_VALUE_UNSET,
    compiler_config_override: Optional[Dict[str, Any]] = None,
) -> YoloBm1688ExportRowResponse:
    from labelu.internal.common.bm1688_quality_report import (
        parse_quality_report,
        quality_summary_one_liner,
    )

    onnx_fn = Path(row.onnx_rel_path).name if row.onnx_rel_path else None
    if compiler_config_override is None:
        bm_names = _list_bmodel_names(row.id)
        bm_fn = _pick_primary_bmodel_name(bm_names)
    else:
        bm_names = _list_bmodel_names(row.id, compiler_config_override)
        bm_fn = _pick_primary_bmodel_name(bm_names, compiler_config_override)
    if not bm_fn and row.bmodel_rel_path:
        bm_fn = Path(row.bmodel_rel_path).name
    qr = parse_quality_report(getattr(row, "quality_report", None))
    edge_validation = None
    assessment_payload = None
    pt_to_bmodel_comparison = None
    dataset_category_fields = {
        "dataset_category_id": None,
        "dataset_category_key": None,
        "dataset_category_name": None,
    }
    contract_fields = {
        "class_names": [],
        "label_set_signature": None,
        "class_order_signature": None,
        "data_yaml_sha256": None,
        "dataset_content_signature": None,
        "training_recipe_key": None,
        "training_recipe_version": None,
        "runtime_profile": None,
        "runtime_interface_version": None,
    }
    if db is not None:
        from labelu.internal.application.service import (
            yolo_aicm_publish as yolo_aicm_publish_service,
        )
        from labelu.internal.application.service import yolo_train as yolo_train_service
        from labelu.internal.application.service.yolo_quality_assessment import (
            build_assessment_payload,
            quality_policy_snapshot_for_job,
        )

        qr = None
        if include_board_report:
            from labelu.internal.common.bm1688_quality_report import (
                parse_quality_report,
            )

            qr = parse_quality_report(getattr(row, "quality_report", None))
        if edge_validation_override is _CATALOG_VALUE_UNSET:
            edge_validation = (
                yolo_aicm_publish_service._edge_validation_status_for_export(
                    db,
                    export_id=row.id,
                )
            )
        else:
            edge_validation = edge_validation_override
        pt_to_bmodel_comparison = _effective_pt_bmodel_comparison(
            row,
            edge_validation,
        )
        job = (
            crud_yolo_train_job.get(db, row.yolo_train_job_id)
            if job_override is _CATALOG_VALUE_UNSET
            else job_override
        )
        task = (
            crud_task.get(db=db, task_id=row.task_id)
            if task_override is _CATALOG_VALUE_UNSET
            else task_override
        )
        # CRUD adapters normally return a persisted ORM row.  Keep the
        # response builder defensive for adapter-level tests and degraded
        # database mocks: a synthetic object must not make the whole export
        # detail endpoint fail while the basic edge status is still usable.
        if job is not None and isinstance(getattr(job, "id", None), int):
            dataset_category_fields = yolo_train_service._training_task_response_fields(
                db, job, task_name
            )
            contract_fields = yolo_train_service._job_contract_response_fields(job)
            task_cfg = (
                yolo_train_service._parse_task_config(task.config)
                if task is not None
                else {}
            )
            task_rect_preset_slug = (
                str(task_cfg.get("defaultRectPresetId") or "").strip() or None
            )
            assessment_payload = build_assessment_payload(
                training_val_metrics=yolo_train_service._training_val_metrics_for_job(
                    job
                ),
                training_assessment_override=(
                    yolo_train_service._composite_training_assessment_for_job(job)
                ),
                test_val_metrics=yolo_train_service._test_val_metrics_for_job(job),
                val_test_comparison=yolo_train_service._val_test_comparison_for_job(
                    job
                ),
                total_sample_count=yolo_train_service._sample_counts_for_job(job).get(
                    "total"
                ),
                edge_validation=edge_validation,
                rect_preset_slug=task_rect_preset_slug,
                pt_to_bmodel_comparison=pt_to_bmodel_comparison,
                pt_to_bmodel_comparison_expected=True,
                quality_policy_snapshot=(
                    quality_policy_snapshot_for_job(job)
                    if quality_policy_snapshot_override is _CATALOG_VALUE_UNSET
                    else quality_policy_snapshot_override
                ),
            )
    gate_label = (
        assessment_payload.training_assessment.status_label
        if assessment_payload is not None
        else (
            _validation_gate_label_for_job(db, row.yolo_train_job_id)
            if db is not None
            else ""
        )
    )
    try:
        artifact_manifest = (
            json.loads(getattr(row, "artifact_manifest_json", None) or "{}") or None
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        artifact_manifest = None
    try:
        quality_gate = (
            json.loads(getattr(row, "quality_gate_json", None) or "{}") or None
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        quality_gate = None
    compatibility_summary = _plate_compatibility_summary(
        row,
        edge_validation,
        artifact_manifest,
        training_recipe_key=contract_fields["training_recipe_key"],
        bmodel_names=bm_names,
    )
    is_superuser = (
        bool(current_user_is_superuser)
        if current_user_is_superuser is not None
        else bool(current_user and is_task_superuser(current_user))
    )
    artifact_manifest = _export_payload_for_user(
        artifact_manifest, current_user, is_superuser=is_superuser
    )
    if not include_board_report:
        artifact_manifest = _compact_artifact_manifest(artifact_manifest)
    quality_gate = _export_payload_for_user(
        quality_gate, current_user, is_superuser=is_superuser
    )
    edge_validation = _redact_board_validation_status_for_user(
        edge_validation, current_user, is_superuser=is_superuser
    )
    response_board_validation = edge_validation
    if (
        not include_board_report
        and edge_validation_override is not _CATALOG_VALUE_UNSET
        and edge_validation is not None
    ):
        board_payload = edge_validation.dict()
        board_payload["report"] = None
        board_payload["train_board_comparison"] = None
        response_board_validation = BoardValidationStatusResponse(**board_payload)

    return YoloBm1688ExportRowResponse(
        id=row.id,
        strategy_task=(
            strategy_task_marker(
                crud_strategy_task_integration.get_by_related_export_id(
                    db, export_id=int(row.id)
                )
                if strategy_task_override is _CATALOG_VALUE_UNSET
                else strategy_task_override
            )
            if db is not None
            else None
        ),
        task_id=row.task_id,
        task_name=task_name,
        yolo_train_job_id=row.yolo_train_job_id,
        dataset_category_id=dataset_category_fields["dataset_category_id"],
        dataset_category_key=dataset_category_fields["dataset_category_key"],
        dataset_category_name=dataset_category_fields["dataset_category_name"],
        **contract_fields,
        status=row.status,
        can_recompile_bmodel=_can_recompile_bmodel(row, db=db),
        imgsz=int(row.imgsz or 640),
        onnx_filename=onnx_fn,
        bmodel_filename=bm_fn,
        bmodel_filenames=bm_names,
        artifact_manifest=artifact_manifest,
        compatibility_summary=compatibility_summary,
        quality_gate=quality_gate,
        error_message=_export_payload_for_user(
            row.error_message, current_user, is_superuser=is_superuser
        ),
        doc_hint=BM1688_DOC_HINT,
        quality_summary=quality_summary_one_liner(qr),
        training_assessment=(
            assessment_payload.training_assessment if assessment_payload else None
        ),
        edge_validation_assessment=(
            assessment_payload.edge_validation_assessment
            if assessment_payload
            else None
        ),
        release_gate_assessment=(
            assessment_payload.release_gate_assessment if assessment_payload else None
        ),
        validation_gate_label=gate_label,
        edge_validation_label=_edge_validation_label(edge_validation),
        edge_execution_label=_edge_execution_label(edge_validation),
        edge_int8_eval_label=_edge_int8_eval_label(row, edge_validation),
        workflow_stage=_workflow_stage(row, edge_validation, gate_label),
        edge_result_status=_edge_result_status(row, edge_validation),
        verdict_code=_verdict_code(row, gate_label, edge_validation),
        current_verdict_label=_verdict_label_from_code(
            _verdict_code(row, gate_label, edge_validation)
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        board_validation=response_board_validation,
    )


def _compile_timeout_seconds() -> int:
    cfg = cfg_io.load_bm1688_compiler_config()
    mode = str(cfg.get("quantize") or "full").lower()
    n_plat = max(1, len(_compile_platforms()))
    base = max(60, int(settings.YOLO_BM1688_COMPILE_TIMEOUT_SECONDS))
    if mode in ("int8", "full"):
        return max(base, 7200) * n_plat
    return base * n_plat


def _builtin_compile_script() -> Path:
    import labelu

    return (
        Path(labelu.__file__).resolve().parent / "scripts" / "bm1688_onnx_to_bmodel.sh"
    )


def _compile_skip_reason() -> str:
    """当 resolve_compile_for_export 返回 do_compile=False 时的说明。"""
    file_cfg = cfg_io.load_bm1688_compiler_config()
    if (file_cfg.get("custom_compile_command") or "").strip():
        return ""
    if (
        settings.YOLO_BM1688_COMPILE_COMMAND or ""
    ).strip() and settings.YOLO_BM1688_COMPILE_ENABLED:
        return ""
    root = (file_cfg.get("tpu_mlir_root") or "").strip()
    if not bool(file_cfg.get("auto_compile", True)):
        return (
            "已跳过 bmodel 编译：auto_compile=false。"
            "请在 BM1688 导出页勾选「导出 ONNX 后自动编译 bmodel」并保存设置。"
        )
    if not root:
        return (
            "已跳过 bmodel 编译：tpu_mlir_root 为空。"
            "请填写 TPU_MLIR_ROOT 并保存，或重启 labelu 以触发部署自动探测。"
        )
    script = _builtin_compile_script()
    if not script.is_file():
        return f"已跳过 bmodel 编译：内置脚本不存在 {script}"
    return ""


def _compile_error_summary(
    compile_log_parts: List[str], compile_exit_code: Optional[int]
) -> str:
    if compile_exit_code in (None, 0):
        return ""
    for line in reversed(compile_log_parts):
        low = line.lower()
        if "error" in low or "traceback" in low or "modulenotfounderror" in low:
            return line[:500]
    return f"TPU-MLIR 编译退出码 {compile_exit_code}"


def _finalize_bmodel_status(
    export_id: int,
    out_root: Path,
    *,
    compile_log_parts: List[str],
    bmodel_rel: Optional[str],
    compile_exit_code: Optional[int] = None,
    model_stem: Optional[str] = None,
) -> Tuple[str, Optional[str], str, str]:
    """根据磁盘上的 bmodel 与编译模式计算最终状态并拼接 compile_log。"""
    bm_names = _list_bmodel_names(export_id)
    primary = _pick_primary_bmodel_name(bm_names)
    if primary and not bmodel_rel:
        bmodel_rel = (
            (_export_dir_for_row(export_id) / "models" / primary)
            .relative_to(Path(settings.MEDIA_ROOT).resolve())
            .as_posix()
        )

    if bm_names:
        compile_log_parts.append(
            "ARTIFACTS_JSON=" + json.dumps({"bmodels": bm_names}, ensure_ascii=False)
        )

    file_cfg = cfg_io.load_bm1688_compiler_config()
    cfg_mode = str(file_cfg.get("quantize") or "full").lower()
    expected_stem = str(model_stem or _bmodel_basename(file_cfg)).strip()
    if not expected_stem:
        expected_stem = cfg_io.DEFAULT_BMODEL_BASENAME
    if cfg_mode == "full":
        have_all = all(
            _platform_has_full_set(out_root, chip, model_stem=expected_stem)
            for chip in _compile_platforms()
        )
        final_status = "COMPLETED" if have_all else "ONNX_READY"
        if bm_names and not have_all:
            compile_log_parts.append(
                "WARN: full 模式要求每平台 models/<PLATFORM>/ 下 fp32、fp16、int8 各 1 个 bmodel；"
                f"platforms={_compile_platforms()} 当前={bm_names}"
            )
    else:
        final_status = "COMPLETED" if bmodel_rel else "ONNX_READY"

    if compile_exit_code not in (None, 0) and final_status != "COMPLETED":
        compile_log_parts.append(
            "ERROR: 编译未成功；请查看上方 exit_code / Traceback，"
            "修复后点「编译 bmodel」或运行 labelu bm1688-recompile"
        )

    compile_log = "\n".join(compile_log_parts)[:12000] if compile_log_parts else None
    compile_err = _compile_error_summary(compile_log_parts, compile_exit_code)
    return final_status, bmodel_rel, compile_log or "", compile_err


def _compile_subprocess_env(file_cfg: dict) -> dict:
    """编译子进程环境：仅用 venv-tpu-mlir，避免 PATH 落到源码 tools / envsetup。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    tpu_py = str(file_cfg.get("tpu_mlir_python") or "").strip()
    if tpu_py:
        py_dir = str(Path(tpu_py).parent)
        env["TPU_MLIR_PYTHON"] = tpu_py
        env["PATH"] = f"{py_dir}:/usr/bin:/bin:/usr/local/bin"
    root = str(file_cfg.get("tpu_mlir_root") or env.get("TPU_MLIR_ROOT") or "").strip()
    if root:
        env["TPU_MLIR_ROOT"] = root
        env["RELEASE_PATH"] = root
        env["PROJECT_ROOT"] = root
        env["TPUC_ROOT"] = root
    return env


def _preflight_tpu_mlir_python(py: str) -> Optional[str]:
    """编译前检查 venv-tpu-mlir 工具是否可用（避免编译脚本内触发 pip 重装）。"""
    from labelu.internal.common.bm1688_deploy_bootstrap import discover_tpu_mlir_python

    py = (py or "").strip()
    from labelu.internal.common.bm1688_deploy_bootstrap import (
        _is_invalid_tpu_mlir_python,
    )

    if _is_invalid_tpu_mlir_python(py):
        py = discover_tpu_mlir_python() or ""
    if not py:
        py = discover_tpu_mlir_python()
    if not py:
        return "tpu_mlir_python 未配置（需要 labeIuMedia/venv-tpu-mlir/bin/python3）"
    py_path = Path(py)
    if not py_path.is_file():
        return f"tpu_mlir_python 不存在: {py}"
    bindir = py_path.parent
    mt = bindir / "model_transform.py"
    if not mt.is_file():
        return f"缺少 {mt}，请执行: bash scripts/repair_tpu_mlir_venv.sh"
    env = {**os.environ, "PYTHONPATH": ""}
    try:
        proc = subprocess.run(
            [py, str(mt), "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "model_transform.py --help 超时"
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-800:]
        return tail or f"model_transform.py --help 退出码 {proc.returncode}"
    try:
        torch_proc = subprocess.run(
            [py, "-c", "import torch"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": ""},
        )
    except subprocess.TimeoutExpired:
        return "import torch 超时"
    if torch_proc.returncode != 0:
        pip_env = {**os.environ, "PYTHONPATH": ""}
        pip_tmp = Path(settings.MEDIA_ROOT) / ".pip_build" / "tmp"
        pip_cache = Path(settings.MEDIA_ROOT) / ".pip_build" / "cache"
        pip_tmp.mkdir(parents=True, exist_ok=True)
        pip_cache.mkdir(parents=True, exist_ok=True)
        pip_env["TMPDIR"] = str(pip_tmp)
        pip_env["PIP_CACHE_DIR"] = str(pip_cache)
        try:
            subprocess.run(
                [
                    py,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "torch",
                    "--index-url",
                    "https://download.pytorch.org/whl/cpu",
                ],
                capture_output=True,
                text=True,
                timeout=900,
                env=pip_env,
                check=False,
            )
            torch_proc = subprocess.run(
                [py, "-c", "import torch"],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": ""},
            )
        except subprocess.TimeoutExpired:
            return (
                "pip install torch 超时；请执行: bash scripts/repair_tpu_mlir_venv.sh"
            )
        if torch_proc.returncode != 0:
            return (
                "venv-tpu-mlir 缺少 torch（ONNX 编译必需）。"
                "请执行: bash scripts/repair_tpu_mlir_venv.sh"
            )
    return None


def _run_bmodel_compile_phase(
    export_id: int,
    dest_onnx: Path,
    out_root: Path,
    imgsz: int,
    *,
    yolo_train_job_id: Optional[int] = None,
    model_weights: Optional[str] = None,
    compile_writer: Optional[Any] = None,
    quantize_writer: Optional[Any] = None,
) -> Tuple[str, Optional[str], str, str]:
    """执行 TPU-MLIR 编译；返回 (final_status, bmodel_rel, compile_log, compile_error)。"""
    compile_log_parts: List[str] = []
    own_compile_w = own_quant_w = None
    if compile_writer is None or quantize_writer is None:
        own_compile_w, own_quant_w = _open_bm1688_process_log_writers(export_id)
        compile_writer = compile_writer or own_compile_w
        quantize_writer = quantize_writer or own_quant_w

    cw, qw = compile_writer, quantize_writer

    try:
        return _run_bmodel_compile_phase_inner(
            export_id,
            dest_onnx,
            out_root,
            imgsz,
            yolo_train_job_id=yolo_train_job_id,
            model_weights=model_weights,
            compile_log_parts=compile_log_parts,
            compile_writer=cw,
            quantize_writer=qw,
        )
    finally:
        if own_compile_w is not None:
            own_compile_w.close()
        if own_quant_w is not None:
            own_quant_w.close()


def _run_bmodel_compile_phase_inner(
    export_id: int,
    dest_onnx: Path,
    out_root: Path,
    imgsz: int,
    *,
    yolo_train_job_id: Optional[int],
    model_weights: Optional[str],
    compile_log_parts: List[str],
    compile_writer: Any,
    quantize_writer: Any,
) -> Tuple[str, Optional[str], str, str]:
    bmodel_rel: Optional[str] = None
    file_cfg = cfg_io.load_bm1688_compiler_config()
    stem = ultralytics_weights_to_sophon_stem(model_weights)
    compile_overrides: dict[str, Any] = {
        "bmodel_basename": stem,
        "qtable_filename": qtable_filename_for_stem(stem),
    }
    mode = str(file_cfg.get("quantize") or "full").strip().lower()
    cali = ""
    if mode in ("int8", "full"):
        work_root = (
            _job_work_root(int(yolo_train_job_id)) if yolo_train_job_id else None
        )
        cali, cali_note = resolve_export_calibration_dir(
            export_root=out_root,
            train_job_work_root=work_root,
            file_cfg=file_cfg,
        )
        compile_overrides["calibration_dataset_dir"] = cali
        picked_n = count_calibration_images(Path(cali)) if cali else 0
        if picked_n > 0:
            compile_overrides["cali_input_num"] = picked_n
        _bm1688_log_part(
            compile_log_parts,
            f"calibration_source={cali_note}",
            compile_writer=compile_writer,
            quantize_writer=quantize_writer,
            quantize_line=True,
        )
        _flush_compile_log(export_id, compile_log_parts)
        if not cali or not Path(cali).is_dir():
            msg = (
                "已跳过 bmodel 编译：INT8 需要校准集。"
                "请完成训练（生成 dataset/images/train）或在编译设置中填写 calibration_dataset_dir。"
            )
            _bm1688_log_part(compile_log_parts, msg, compile_writer=compile_writer)
            logger.warning("bm1688 export_id={} {}", export_id, msg)
            return _finalize_bmodel_status(
                export_id,
                out_root,
                compile_log_parts=compile_log_parts,
                bmodel_rel=None,
                model_stem=stem,
            )
        qt = discover_qtable_path(stem, file_cfg)
        if qt:
            ensure_sigmoid_f16_in_qtable(qt, docker_fpfwd_from_cfg(file_cfg))
            compile_overrides["quantize_table_path"] = str(qt)
            _bm1688_log_part(
                compile_log_parts,
                f"qtable={qt.name} stem={stem}",
                compile_writer=compile_writer,
                quantize_writer=quantize_writer,
                quantize_line=True,
            )
        else:
            _bm1688_log_part(
                compile_log_parts,
                f"WARN: 未找到与 {stem} 匹配的 qtable，INT8 可能精度偏差较大",
                compile_writer=compile_writer,
                quantize_writer=quantize_writer,
                quantize_line=True,
            )

    _bm1688_log_part(
        compile_log_parts, f"sophon_stem={stem}", compile_writer=compile_writer
    )

    backend = quantize_backend_from_cfg(file_cfg)
    _bm1688_log_part(
        compile_log_parts,
        f"quantize_backend={backend}",
        compile_writer=compile_writer,
        quantize_writer=quantize_writer,
        quantize_line=True,
    )
    _flush_compile_log(export_id, compile_log_parts)
    need_int8 = mode in ("int8", "full")
    docker_int8_ready = False
    docker_full_ready = False
    if need_int8 and backend == "docker" and cali and Path(cali).is_dir():
        pt_for_docker: Optional[Path] = None
        if yolo_train_job_id:
            wr = _job_work_root(int(yolo_train_job_id))
            candidate = wr / "train" / "weights" / "best.pt"
            if candidate.is_file():
                pt_for_docker = candidate
        if pt_for_docker and pt_for_docker.is_file():
            plat_dir = cfg_io.sophon_platform_dir(out_root, "bm1688")
            plat_dir.mkdir(parents=True, exist_ok=True)
            _bm1688_log_part(
                compile_log_parts,
                "docker_quant_start",
                compile_writer=compile_writer,
                quantize_writer=quantize_writer,
                quantize_line=True,
            )
            _flush_compile_log(export_id, compile_log_parts)
            docker_streamed = False
            docker_last_flush_at = time.monotonic()
            docker_stream_lines = 0

            def _on_docker_quant_line(line: str, is_stderr: bool = False) -> None:
                nonlocal docker_streamed, docker_last_flush_at, docker_stream_lines
                if not line:
                    return
                docker_streamed = True
                docker_stream_lines += 1
                _bm1688_log_part(
                    compile_log_parts,
                    ("stderr: " if is_stderr else "") + line,
                    compile_writer=compile_writer,
                    quantize_writer=quantize_writer,
                    quantize_line=True,
                )
                now = time.monotonic()
                if (
                    docker_stream_lines == 1
                    or docker_stream_lines % 20 == 0
                    or (now - docker_last_flush_at) >= 1.5
                ):
                    _flush_compile_log(export_id, compile_log_parts)
                    docker_last_flush_at = now

            ok, docker_log = run_docker_int8_quant(
                pt_path=pt_for_docker,
                calib_dir=Path(cali),
                out_dir=plat_dir,
                model_stem=stem,
                file_cfg=file_cfg,
                onnx_path=dest_onnx if dest_onnx.is_file() else None,
                imgsz=imgsz,
                timeout_seconds=_compile_timeout_seconds(),
                line_callback=_on_docker_quant_line,
                emit_fp32_fp16=mode == "full",
            )
            quantize_writer.write_block("docker_int8_quant", docker_log)
            if not docker_streamed and docker_log:
                _bm1688_log_part(
                    compile_log_parts,
                    docker_log[-8000:],
                    compile_writer=compile_writer,
                )
            _flush_compile_log(export_id, compile_log_parts)
            if ok:
                canonical = plat_dir / f"{stem}_int8_1b.bmodel"
                if canonical.is_file():
                    docker_int8_ready = True
                    bmodel_rel = f"models/BM1688/{canonical.name}"
                    _bm1688_log_part(
                        compile_log_parts,
                        f"docker_int8_bmodel={bmodel_rel}",
                        compile_writer=compile_writer,
                        quantize_writer=quantize_writer,
                        quantize_line=True,
                    )
                    docker_full_ready = mode == "full" and all(
                        (plat_dir / f"{stem}_{suffix}_1b.bmodel").is_file()
                        for suffix in ("fp32", "fp16", "int8")
                    )
                docker_qt = plat_dir / f"{stem}_qtable"
                if docker_qt.is_file():
                    onnx_dir = cfg_io.sophon_onnx_dir(out_root)
                    onnx_dir.mkdir(parents=True, exist_ok=True)
                    qt_dest = onnx_dir / qtable_filename_for_stem(stem)
                    shutil.copy2(docker_qt, qt_dest)
                    ensure_sigmoid_f16_in_qtable(
                        qt_dest, docker_fpfwd_from_cfg(file_cfg)
                    )
                    compile_overrides["quantize_table_path"] = str(qt_dest.resolve())
                    _bm1688_log_part(
                        compile_log_parts,
                        f"docker_qtable={qt_dest.name} (search_qtable 自动混精)",
                        compile_writer=compile_writer,
                        quantize_writer=quantize_writer,
                        quantize_line=True,
                    )
                if mode == "int8":
                    return _finalize_bmodel_status(
                        export_id,
                        out_root,
                        compile_log_parts=compile_log_parts,
                        bmodel_rel=bmodel_rel,
                        compile_exit_code=0,
                        model_stem=stem,
                    )
                if docker_full_ready:
                    _bm1688_log_part(
                        compile_log_parts,
                        "docker_full_bmodels=fp32,fp16,int8",
                        compile_writer=compile_writer,
                        quantize_writer=quantize_writer,
                        quantize_line=True,
                    )
                    return _finalize_bmodel_status(
                        export_id,
                        out_root,
                        compile_log_parts=compile_log_parts,
                        bmodel_rel=bmodel_rel,
                        compile_exit_code=0,
                        model_stem=stem,
                    )
            else:
                _bm1688_log_part(
                    compile_log_parts,
                    "WARN: Docker INT8 失败，将尝试宿主机 TPU-MLIR 编译（若已配置）",
                    compile_writer=compile_writer,
                    quantize_writer=quantize_writer,
                    quantize_line=True,
                )
        else:
            _bm1688_log_part(
                compile_log_parts,
                "WARN: Docker INT8 需要训练作业 best.pt，未找到则回退 host 编译",
                compile_writer=compile_writer,
                quantize_writer=quantize_writer,
                quantize_line=True,
            )

    if mode == "full" and docker_int8_ready:
        compile_overrides["quantize"] = "fp32_fp16"
        _bm1688_log_part(
            compile_log_parts,
            "host_compile_mode=fp32_fp16 (Docker INT8 已产出，宿主机仅编译 fp32/fp16)",
            compile_writer=compile_writer,
            quantize_writer=quantize_writer,
            quantize_line=True,
        )

    do_compile, cmd = bm1688_compiler_cfg_service.resolve_compile_for_export(
        onnx_abs=dest_onnx,
        out_dir=out_root,
        imgsz=imgsz,
        compile_overrides=compile_overrides,
    )
    if not do_compile or not cmd:
        skip = _compile_skip_reason() or "未配置 bmodel 编译命令"
        _bm1688_log_part(compile_log_parts, skip, compile_writer=compile_writer)
        logger.warning("bm1688 export_id={} compile skipped: {}", export_id, skip)
        return _finalize_bmodel_status(
            export_id,
            out_root,
            compile_log_parts=compile_log_parts,
            bmodel_rel=None,
            compile_exit_code=None,
            model_stem=stem,
        )

    tpu_py = str(file_cfg.get("tpu_mlir_python") or "").strip()
    pre_err = _preflight_tpu_mlir_python(tpu_py)
    if pre_err:
        _bm1688_log_part(
            compile_log_parts,
            "ERROR: TPU-MLIR 预检失败；请先运行 bash scripts/repair_tpu_mlir_venv.sh\n"
            + pre_err,
            compile_writer=compile_writer,
        )
        logger.warning(
            "bm1688 export_id={} preflight failed: {}", export_id, pre_err[:300]
        )
        return _finalize_bmodel_status(
            export_id,
            out_root,
            compile_log_parts=compile_log_parts,
            bmodel_rel=None,
            compile_exit_code=1,
            model_stem=stem,
        )

    _bm1688_log_part(
        compile_log_parts,
        f"compile_start={datetime.now().isoformat()}",
        compile_writer=compile_writer,
    )
    compile_writer.write_block("compile_command", cmd)
    _bm1688_log_part(
        compile_log_parts,
        f"cmd={cmd[:1200]}",
        compile_writer=compile_writer,
    )
    if mode in ("int8", "full"):
        quantize_writer.write_block("host_compile_command", cmd)
    _flush_compile_log(export_id, compile_log_parts)
    host_quantize_block = str(compile_overrides.get("quantize") or mode).lower() in (
        "int8",
        "full",
        "fp32_fp16",
    )
    compile_exit_code: Optional[int] = None
    try:
        compile_exit_code = _run_compile_shell_command(
            cmd,
            env=_compile_subprocess_env(file_cfg),
            timeout=_compile_timeout_seconds(),
            compile_log_parts=compile_log_parts,
            compile_writer=compile_writer,
            quantize_writer=quantize_writer,
            quantize_block=host_quantize_block,
        )
        _bm1688_log_part(
            compile_log_parts,
            f"exit_code={compile_exit_code}",
            compile_writer=compile_writer,
        )
    except subprocess.TimeoutExpired:
        _bm1688_log_part(
            compile_log_parts,
            f"compile command timeout ({_compile_timeout_seconds()}s)",
            compile_writer=compile_writer,
        )
        compile_exit_code = -1
    except Exception as exc:
        _bm1688_log_part(
            compile_log_parts,
            f"compile hook error: {exc}",
            compile_writer=compile_writer,
        )
        compile_exit_code = -1

    return _finalize_bmodel_status(
        export_id,
        out_root,
        compile_log_parts=compile_log_parts,
        bmodel_rel=bmodel_rel,
        compile_exit_code=compile_exit_code,
        model_stem=stem,
    )


def _run_bm1688_export_sync(export_id: int) -> None:
    from labelu.internal.common.bm1688_deploy_bootstrap import (
        ensure_bm1688_compiler_ready,
    )
    from labelu.internal.common.db import session_scope

    with session_scope() as dispatch_db:
        # 这里只读取路由所需的两列，迁移窗口中旧库可能尚未有新动作字段。
        dispatch_row = (
            dispatch_db.query(
                YoloBm1688Export.id,
                YoloBm1688Export.yolo_train_job_id,
            )
            .filter(YoloBm1688Export.id == int(export_id))
            .first()
        )
        dispatch_job = (
            crud_yolo_train_job.get(dispatch_db, dispatch_row.yolo_train_job_id)
            if dispatch_row
            else None
        )
        dispatch_job_type = ensure_phase_one_training_job_type(
            getattr(dispatch_job, "job_type", None)
        )
        if dispatch_job_type == "plate_detection":
            from labelu.internal.application.service.plate_detection_bm1688 import (
                run_plate_detection_bm1688_export,
            )

            run_plate_detection_bm1688_export(export_id)
            return
        if dispatch_job_type == "plate_recognition":
            from labelu.internal.application.service.plate_recognition_bm1688 import (
                run_plate_recognition_bm1688_export,
            )

            run_plate_recognition_bm1688_export(export_id)
            return

    execution_guard = _acquire_export_execution_guard(export_id)
    if execution_guard is None:
        logger.warning("skip duplicate BM1688 export execution export_id={}", export_id)
        return
    compile_w = None
    quantize_w = None
    execution_monitor = None
    execution_fencing_enabled = False
    try:
        # The dispatcher lease only hands work to this process.  Claim a
        # separate execution lease before any compiler side effect.  During
        # the migration window only an explicit missing-column signal may use
        # the legacy path; lock/connection errors remain failures.
        owner = f"bm1688-export:{os.getpid()}:{threading.get_ident()}:{export_id}"
        fencing_unavailable = False
        claim_row_exists = False
        try:
            with session_scope() as fence_db:
                claim_row_exists = (
                    fence_db.query(YoloBm1688Export)
                    .filter(YoloBm1688Export.id == int(export_id))
                    .first()
                    is not None
                )
                execution_token = claim_execution_lease(
                    fence_db,
                    YoloBm1688Export,
                    int(export_id),
                    owner=owner,
                    lease_seconds=900,
                )
        except ExecutionFencingUnavailable:
            execution_token = None
            fencing_unavailable = True
            logger.warning(
                "BM1688 export execution fencing columns unavailable; "
                "using legacy compatibility path export_id={}",
                export_id,
            )
        if execution_token is None and not fencing_unavailable and claim_row_exists:
            execution_guard.release()
            logger.warning(
                "skip BM1688 export claimed by another worker export_id={}", export_id
            )
            return
        if execution_token is not None:
            execution_fencing_enabled = True
            execution_monitor = ExecutionLeaseMonitor(
                execution_token,
                session_scope_factory=session_scope,
                label=f"bm1688-export-{export_id}",
            ).start()
            execution_monitor.ensure_current()
        # Keep setup under the same ownership boundary as the main export.  A
        # missing compiler or unwritable log directory must not leave the
        # in-process/MySQL execution guard held forever.
        ensure_bm1688_compiler_ready()
        ensure_phase_one_bm1688_platforms(_compile_platforms())
        rel_onnx = ""
        compile_w, quantize_w = _open_bm1688_process_log_writers(export_id)
        progress_log: List[str] = [
            f"export_start={datetime.now().isoformat()}",
            "phase=onnx_export",
            f"process_log_compile={compile_w.path}",
            f"process_log_quantize={quantize_w.path}",
        ]
        compile_w.write("phase=onnx_export")
    except Exception:
        for writer in (compile_w, quantize_w):
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    logger.exception(
                        "close BM1688 setup log writer failed export_id={}",
                        export_id,
                    )
        execution_guard.release()
        raise
    try:
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            if not row:
                return
            job = crud_yolo_train_job.get(db, row.yolo_train_job_id)
            if (
                not job
                or job.status != YoloTrainStatus.COMPLETED.value
                or not (job.weights_path or "").strip()
            ):
                failure_updates = {
                    "status": "FAILED",
                    "error_message": "训练作业未完成或缺少权重路径",
                }
                if execution_monitor is not None:
                    execution_monitor.conditional_update(
                        failure_updates,
                        db=db,
                        manage_transaction=True,
                    )
                else:
                    with begin_transaction(db):
                        crud_yolo_bm1688_export.update_row(
                            db,
                            row,
                            **failure_updates,
                        )
                return

            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    {"status": "PROCESSING", "error_message": None},
                    db=db,
                    manage_transaction=True,
                )
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(db, row, status="PROCESSING")
            _persist_export_progress(
                export_id,
                progress_log,
                status="PROCESSING",
                error_message=None,
                execution_monitor=execution_monitor,
            )

            pt_path = resolve_weights_path(job.weights_path)
            out_root = _export_dir_for_row(export_id)
            out_root.mkdir(parents=True, exist_ok=True)

            if not ensure_ultralytics_installed(reason="BM1688 ONNX 导出"):
                failure_updates = {
                    "status": "FAILED",
                    "error_message": "当前环境未安装 ultralytics，无法导出 ONNX",
                }
                if execution_monitor is not None:
                    execution_monitor.conditional_update(
                        failure_updates,
                        db=db,
                        manage_transaction=True,
                    )
                else:
                    with begin_transaction(db):
                        crud_yolo_bm1688_export.update_row(
                            db,
                            row,
                            **failure_updates,
                        )
                return

            imgsz = int(job.imgsz or 640)
            export_task_id = int(row.task_id)
            export_train_job_id = int(row.yolo_train_job_id)
            model_weights = job.model_weights
            file_cfg = cfg_io.load_bm1688_compiler_config()
            use_sophon = bool(file_cfg.get("sophon_export", True))
            stem = ultralytics_weights_to_sophon_stem(model_weights)
            onnx_fn = onnx_filename_for_stem(stem)
            qtable_fn = qtable_filename_for_stem(stem)
            onnx_dir = cfg_io.sophon_onnx_dir(out_root)
            onnx_dir.mkdir(parents=True, exist_ok=True)
            dest_onnx = onnx_dir / onnx_fn

        compile_w.write(
            f"onnx_export pt={pt_path} imgsz={imgsz} sophon={use_sophon} dest={dest_onnx}"
        )
        if use_sophon:
            if execution_monitor is not None:
                execution_monitor.ensure_current()
            export_yolo_to_sophon_onnx(
                pt_path,
                imgsz=imgsz,
                dest_dir=onnx_dir,
                dest_basename=dest_onnx.name,
            )
        else:
            from ultralytics import YOLO

            if execution_monitor is not None:
                execution_monitor.ensure_current()
            model = YOLO(str(pt_path))
            exported = model.export(
                format="onnx",
                imgsz=imgsz,
                simplify=True,
                opset=12,
                dynamic=False,
            )
            shutil.copy2(Path(str(exported)), dest_onnx)
        compile_w.write(f"onnx_export_done path={dest_onnx}")

        qt_src = discover_qtable_path(stem, file_cfg)
        if qt_src and qt_src.is_file():
            if is_reused_yolov11s_qtable(qt_src, stem):
                compile_w.write(
                    f"WARN: 跳过复制 qtable（与 yolov11s 模板相同）stem={stem}；"
                    "INT8 将依赖 Docker fp_forward 生成专属 qtable"
                )
            else:
                shutil.copy2(qt_src, onnx_dir / qtable_fn)
                logger.info(
                    "bm1688 export_id={} qtable {} → {}",
                    export_id,
                    qt_src.name,
                    onnx_dir / qtable_fn,
                )

        rel_onnx = dest_onnx.relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix()
        progress_log.append(f"onnx_ready={rel_onnx}")
        progress_log.append("phase=bmodel_compile")
        _persist_export_progress(
            export_id,
            progress_log,
            status="PROCESSING",
            onnx_rel_path=rel_onnx,
            execution_monitor=execution_monitor,
        )

        if execution_monitor is not None:
            execution_monitor.ensure_current()
        final_status, bmodel_rel, compile_log, compile_err = _run_bmodel_compile_phase(
            export_id,
            dest_onnx,
            out_root,
            imgsz,
            yolo_train_job_id=export_train_job_id,
            model_weights=model_weights,
            compile_writer=compile_w,
            quantize_writer=quantize_w,
        )

        if execution_monitor is not None:
            execution_monitor.ensure_current()
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            if not row:
                return
            final_updates = {
                "status": final_status,
                "onnx_rel_path": rel_onnx,
                "bmodel_rel_path": bmodel_rel,
                "compile_log": compile_log,
                "error_message": compile_err or None,
            }
            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    final_updates, db=db, manage_transaction=True
                )
                db.expire(row)
                db.refresh(row)
                _persist_artifact_manifest(db, row, execution_monitor=execution_monitor)
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(db, row, **final_updates)
                    _persist_artifact_manifest(db, row)
            if final_status == "COMPLETED":
                _sync_model_version_artifacts_for_export(db, row)
        if rel_onnx:
            if final_status == "COMPLETED":
                if execution_monitor is not None:
                    execution_monitor.ensure_current()
                try:
                    from labelu.internal.application.service.yolo_sop_model_evaluation import (
                        run_server_sop_evaluations_for_export,
                    )

                    run_server_sop_evaluations_for_export(
                        export_id,
                        yolo_train_job_id=export_train_job_id,
                        imgsz=imgsz,
                        execution_monitor=execution_monitor,
                    )
                except ExecutionFencingLost:
                    raise
                except Exception:
                    logger.exception(
                        "SOP server eval failed export_id={} (pt/onnx 评估)",
                        export_id,
                    )
                _maybe_run_edge_ssh_validation_sync(
                    export_id,
                    yolo_train_job_id=export_train_job_id,
                    imgsz=imgsz,
                    execution_monitor=execution_monitor,
                )
            _generate_quality_report_sync(
                export_id,
                run_onnx_val=False,
                execution_monitor=execution_monitor,
            )
    except ExecutionFencingLost as e:
        logger.error("BM1688 export fencing lost export_id={} error={}", export_id, e)
        compile_w.write_block("execution_fencing_lost", str(e)) if compile_w else None
    except Exception as e:
        logger.exception("yolo bm1688 export failed export_id={}", export_id)
        compile_w.write_block("export_failure", str(e))
        try:
            with session_scope() as db:
                row = crud_yolo_bm1688_export.get(db, export_id)
                if row:
                    if execution_monitor is not None:
                        execution_monitor.conditional_update(
                            {"status": "FAILED", "error_message": str(e)[:8000]},
                            db=db,
                            manage_transaction=True,
                        )
                    else:
                        with begin_transaction(db):
                            crud_yolo_bm1688_export.update_row(
                                db,
                                row,
                                status="FAILED",
                                error_message=str(e)[:8000],
                            )
        except Exception:
            logger.exception("bm1688 export persist failure")
    finally:
        compile_w.close()
        quantize_w.close()
        execution_guard.release()
        if execution_monitor is not None:
            execution_monitor.stop()
            try:
                with session_scope() as fence_db:
                    from labelu.internal.common.execution_fencing import (
                        release_execution_lease,
                    )

                    release_execution_lease(fence_db, execution_monitor.token)
            except Exception:
                logger.exception(
                    "release BM1688 execution lease failed export_id={}", export_id
                )


def create_export_for_job_system(
    db: Session,
    *,
    task_id: int,
    job_id: int,
    user_id: int,
) -> tuple[YoloBm1688Export, bool]:
    """Create one export for an internal automatic trigger.

    The caller must hold the training-job row lock when concurrent workers are
    possible. Existing records are preserved so automatic scans are idempotent.
    """
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    task = crud_task.get(db=db, task_id=int(task_id))
    job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
    if task is None:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job is None or int(job.task_id) != int(task_id):
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    ensure_phase_one_training_job_type(getattr(job, "job_type", None))
    ensure_phase_one_bm1688_platforms(_compile_platforms())
    try:
        assert_project_chain(
            [getattr(task, "project_id", None), getattr(job, "project_id", None)],
            context="BM1688 export training job",
        )
    except ProjectScopeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="训练作业与任务不属于同一项目，禁止导出",
        ) from exc
    if (
        job.status != YoloTrainStatus.COMPLETED.value
        or not (job.weights_path or "").strip()
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57008_YOLO_EVAL_JOB_NOT_COMPLETED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练未完成或还未生成 best.pt，无法导出算能盒文件",
        )
    existing = crud_yolo_bm1688_export.list_all_for_job(
        db, yolo_train_job_id=int(job_id)
    )
    if existing:
        return existing[0], False
    row = crud_yolo_bm1688_export.create(
        db,
        user_id=int(user_id),
        task_id=int(task_id),
        yolo_train_job_id=int(job_id),
        imgsz=int(job.imgsz or 640),
        worker_action="export",
    )
    return row, True


def submit_export_background(export_id: int) -> None:
    """Run an export without requiring an asyncio event loop."""
    if dedicated_bm1688_worker_enabled():
        # 独立 BM1688 Worker 会按 worker_action 领取 PENDING 记录，Web
        # 进程不能再启动第二条线程，避免同一导出并发执行。
        return
    threading.Thread(
        target=_run_bm1688_export_sync,
        args=(int(export_id),),
        name=f"bm1688-export-{int(export_id)}",
        daemon=True,
    ).start()


def dedicated_bm1688_worker_enabled() -> bool:
    """Return whether BM1688 work should remain in the dedicated worker queue."""
    from labelu.internal.application.service.bm1688_worker_runtime import (
        dedicated_worker_enabled,
    )

    return dedicated_worker_enabled()


async def start_export_for_job(
    db: Session,
    *,
    task_id: int,
    job_id: int,
    current_user: User,
) -> YoloBm1688ExportRowResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_train_job.get(db, job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    ensure_phase_one_training_job_type(getattr(job, "job_type", None))
    ensure_phase_one_bm1688_platforms(_compile_platforms())
    try:
        assert_project_chain(
            [getattr(task, "project_id", None), getattr(job, "project_id", None)],
            context="BM1688 export training job",
        )
    except ProjectScopeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="训练作业与任务不属于同一项目，禁止导出",
        ) from exc
    if (
        job.status != YoloTrainStatus.COMPLETED.value
        or not (job.weights_path or "").strip()
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57008_YOLO_EVAL_JOB_NOT_COMPLETED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练未完成或还未生成 best.pt，无法导出算能盒文件",
        )

    if crud_yolo_bm1688_export.has_processing_for_job(db, yolo_train_job_id=job_id):
        raise LabelUException(
            code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
            status_code=status.HTTP_409_CONFLICT,
        )

    with begin_transaction(db):
        row = crud_yolo_bm1688_export.create(
            db,
            user_id=current_user.id,
            task_id=task_id,
            yolo_train_job_id=job_id,
            imgsz=int(job.imgsz or 640),
            worker_action="export",
        )
        export_id = row.id

    loop = asyncio.get_event_loop()
    if dedicated_bm1688_worker_enabled():
        # 独立 Worker 会按 worker_action 领取；Web 进程只负责入队。
        pass
    else:
        loop.run_in_executor(None, _run_bm1688_export_sync, export_id)

    fresh = crud_yolo_bm1688_export.get(db, export_id)
    return _export_row_to_response(
        fresh or row,
        task.name,
        current_user=current_user,
    )


@dataclass(frozen=True)
class _CompileFamilyIdentity:
    task_id: int
    algorithm_token: str

    @property
    def family_key(self) -> str:
        return f"task:{self.task_id}:{self.algorithm_token}"


@dataclass(frozen=True)
class _CompileJobLineage:
    job_id: int
    identity: _CompileFamilyIdentity
    algorithm_id: Optional[int]
    algorithm_key: str
    grouping_source: str
    model_version_id: Optional[int]
    version_no: Optional[int]
    dataset_version_id: Optional[int]
    dataset_version_no: Optional[int]


def _compile_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _compile_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _compile_job_lineages(
    db: Session, job_ids: set[int]
) -> dict[int, _CompileJobLineage]:
    """Resolve compile rows to model families, including pre-ModelVersion rows.

    New rows use ModelVersion.training_job_id as the authoritative algorithm
    lineage. Legacy rows first reuse algorithm_id/algorithm_key from the frozen
    training parameters. If neither exists, a conservative fingerprint of the
    job type, category and recipe keeps distinguishable legacy algorithms in
    separate task-local families instead of merging them into one unknown bucket.
    """

    normalized_ids = {int(job_id) for job_id in job_ids if job_id}
    if not normalized_ids:
        return {}
    jobs = db.query(YoloTrainJob).filter(YoloTrainJob.id.in_(normalized_ids)).all()
    models = (
        db.query(ModelVersion)
        .filter(ModelVersion.training_job_id.in_(normalized_ids))
        .order_by(ModelVersion.id.desc())
        .all()
    )
    model_by_job: dict[int, ModelVersion] = {}
    for model in models:
        if model.training_job_id:
            model_by_job.setdefault(int(model.training_job_id), model)

    parsed_by_job: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    algorithm_ids = {
        int(model.algorithm_id) for model in models if model.algorithm_id is not None
    }
    algorithm_keys: set[str] = set()
    dataset_version_ids = {
        int(model.dataset_version_id)
        for model in models
        if model.dataset_version_id is not None
    }
    for job in jobs:
        params = _compile_json_object(getattr(job, "hyperparams_json", None))
        manifest = _compile_json_object(getattr(job, "dataset_manifest_json", None))
        parsed_by_job[int(job.id)] = (params, manifest)
        algorithm_id = _compile_positive_int(params.get("algorithm_id"))
        if algorithm_id is not None:
            algorithm_ids.add(algorithm_id)
        algorithm_key = str(params.get("algorithm_key") or "").strip().lower()
        if algorithm_key:
            algorithm_keys.add(algorithm_key)
        dataset_contract = params.get("dataset_manifest")
        dataset_version_id = _compile_positive_int(
            params.get("dataset_version_id")
            or manifest.get("dataset_version_id")
            or (
                dataset_contract.get("dataset_version_id")
                if isinstance(dataset_contract, dict)
                else None
            )
        )
        if dataset_version_id is not None:
            dataset_version_ids.add(dataset_version_id)

    algorithms = []
    if algorithm_ids:
        algorithms.extend(
            db.query(AlgorithmDefinition)
            .filter(AlgorithmDefinition.id.in_(algorithm_ids))
            .all()
        )
    known_algorithm_ids = {int(row.id) for row in algorithms}
    if algorithm_keys:
        algorithms.extend(
            db.query(AlgorithmDefinition)
            .filter(AlgorithmDefinition.algorithm_key.in_(algorithm_keys))
            .filter(~AlgorithmDefinition.id.in_(known_algorithm_ids))
            .all()
        )
    algorithm_by_id = {int(row.id): row for row in algorithms}
    algorithm_by_key = {
        str(row.algorithm_key or "").strip().lower(): row for row in algorithms
    }
    dataset_versions = (
        db.query(DatasetVersion)
        .filter(DatasetVersion.id.in_(dataset_version_ids))
        .all()
        if dataset_version_ids
        else []
    )
    dataset_version_no_by_id = {
        int(row.id): int(row.version_no) for row in dataset_versions
    }

    result: dict[int, _CompileJobLineage] = {}
    for job in jobs:
        job_id = int(job.id)
        task_id = int(job.task_id)
        model = model_by_job.get(job_id)
        params, manifest = parsed_by_job[job_id]
        explicit_algorithm_id = _compile_positive_int(params.get("algorithm_id"))
        explicit_algorithm_key = str(params.get("algorithm_key") or "").strip().lower()

        if model is not None:
            algorithm_id = int(model.algorithm_id)
            algorithm = algorithm_by_id.get(algorithm_id)
            algorithm_key = (
                str(getattr(algorithm, "algorithm_key", "") or "").strip().lower()
                or explicit_algorithm_key
                or f"algorithm-{algorithm_id}"
            )
            identity = _CompileFamilyIdentity(task_id, f"algorithm:{algorithm_id}")
            grouping_source = "model_version"
        else:
            algorithm = algorithm_by_id.get(explicit_algorithm_id or -1)
            if algorithm is None and explicit_algorithm_key:
                algorithm = algorithm_by_key.get(explicit_algorithm_key)
            algorithm_id = (
                int(algorithm.id) if algorithm is not None else explicit_algorithm_id
            )
            if algorithm_id is not None:
                algorithm_key = (
                    str(getattr(algorithm, "algorithm_key", "") or "").strip().lower()
                    or explicit_algorithm_key
                    or f"algorithm-{algorithm_id}"
                )
                identity = _CompileFamilyIdentity(task_id, f"algorithm:{algorithm_id}")
            else:
                fallback = {
                    "algorithm_key": explicit_algorithm_key,
                    "job_type": str(getattr(job, "job_type", None) or "unknown")
                    .strip()
                    .lower(),
                    "category": str(getattr(job, "category", None) or "")
                    .strip()
                    .lower(),
                    "training_recipe_key": str(
                        getattr(job, "training_recipe_key", None) or ""
                    )
                    .strip()
                    .lower(),
                }
                fingerprint = hashlib.sha256(
                    json.dumps(fallback, ensure_ascii=True, sort_keys=True).encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                identity = _CompileFamilyIdentity(task_id, f"legacy:{fingerprint}")
                fallback_parts = [value for value in fallback.values() if value]
                algorithm_key = "legacy:" + ":".join(fallback_parts or [str(job_id)])
            grouping_source = "legacy_training_job"

        dataset_contract = params.get("dataset_manifest")
        dataset_version_id = (
            int(model.dataset_version_id)
            if model is not None and model.dataset_version_id is not None
            else _compile_positive_int(
                params.get("dataset_version_id")
                or manifest.get("dataset_version_id")
                or (
                    dataset_contract.get("dataset_version_id")
                    if isinstance(dataset_contract, dict)
                    else None
                )
            )
        )
        result[job_id] = _CompileJobLineage(
            job_id=job_id,
            identity=identity,
            algorithm_id=algorithm_id,
            algorithm_key=algorithm_key,
            grouping_source=grouping_source,
            model_version_id=int(model.id) if model is not None else None,
            version_no=int(model.version_no) if model is not None else None,
            dataset_version_id=dataset_version_id,
            dataset_version_no=(
                dataset_version_no_by_id.get(dataset_version_id)
                if dataset_version_id is not None
                else None
            ),
        )
    return result


def list_export_families_global(
    db: Session,
    current_user: User,
    *,
    yolo_train_job_id: Optional[int] = None,
    export_id: Optional[int] = None,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    export_status: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[YoloBm1688ExportFamilyResponse], int]:
    """Page compile families while returning every export in each selected family.

    Filters and deep links select families. Once a family is selected, its
    complete visible compile history is returned, including multiple exports
    for the same model version.
    """

    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    unrestricted = is_task_superuser(current_user)
    builtin_admin = is_consistent_pool_admin(current_user)
    matching_rows = crud_yolo_bm1688_export.list_all_for_user(
        db,
        current_user.id,
        task_access_unrestricted=unrestricted,
        task_access_builtin_admin=builtin_admin,
        export_status=export_status,
        task_name=task_name,
        search=search,
        yolo_train_job_id=yolo_train_job_id,
        export_id=export_id,
        summary_projection=True,
    )
    if not matching_rows:
        return [], 0
    matching_lineages = _compile_job_lineages(
        db,
        {int(export_row.yolo_train_job_id) for export_row, _task in matching_rows},
    )
    latest_matching_export_by_family: dict[_CompileFamilyIdentity, int] = {}
    for export_row, _task in matching_rows:
        lineage = matching_lineages.get(int(export_row.yolo_train_job_id))
        if lineage is None:
            continue
        latest_matching_export_by_family[lineage.identity] = max(
            int(export_row.id),
            latest_matching_export_by_family.get(lineage.identity, 0),
        )
    ordered_identities = sorted(
        latest_matching_export_by_family,
        key=lambda identity: latest_matching_export_by_family[identity],
        reverse=True,
    )
    total = len(ordered_identities)
    selected_identities = ordered_identities[page * size : (page + 1) * size]
    if not selected_identities:
        return [], total

    # Reload all visible exports for only the selected tasks without the
    # status/deep-link filters. Family identity filtering below keeps other
    # algorithms on the same task isolated while retaining full history.
    selected_identity_set = set(selected_identities)
    selected_task_ids = sorted({identity.task_id for identity in selected_identities})
    task_rows = crud_yolo_bm1688_export.list_all_for_user(
        db,
        current_user.id,
        task_ids=selected_task_ids,
        task_access_unrestricted=unrestricted,
        task_access_builtin_admin=builtin_admin,
    )
    all_lineages = _compile_job_lineages(
        db,
        {int(export_row.yolo_train_job_id) for export_row, _task in task_rows},
    )
    family_rows: dict[
        _CompileFamilyIdentity, list[tuple[Any, Any, _CompileJobLineage]]
    ] = {identity: [] for identity in selected_identities}
    visible_rows = []
    for export_row, task in task_rows:
        lineage = all_lineages.get(int(export_row.yolo_train_job_id))
        if lineage is None or lineage.identity not in selected_identity_set:
            continue
        family_rows[lineage.identity].append((export_row, task, lineage))
        visible_rows.append((export_row, task))

    edge_validations, jobs = _export_catalog_context(db, visible_rows)
    policy_snapshots = _quality_policy_snapshots_for_export_rows(visible_rows)
    strategy_tasks = crud_strategy_task_integration.get_by_related_export_ids(
        db, export_ids=(export_row.id for export_row, _task in visible_rows)
    )
    compiler_config = cfg_io.load_bm1688_compiler_config() if visible_rows else None
    response_by_export_id: dict[int, YoloBm1688ExportFamilyItemResponse] = {}
    for export_row, task in visible_rows:
        lineage = all_lineages[int(export_row.yolo_train_job_id)]
        base = _export_row_to_response(
            export_row,
            task.name,
            db,
            current_user=current_user,
            edge_validation_override=edge_validations.get(int(export_row.id)),
            job_override=jobs.get(int(export_row.yolo_train_job_id)),
            task_override=task,
            quality_policy_snapshot_override=policy_snapshots.get(
                _rect_preset_slug_for_task(task)
            ),
            current_user_is_superuser=unrestricted,
            strategy_task_override=strategy_tasks.get(int(export_row.id)),
            compiler_config_override=compiler_config,
        )
        response_by_export_id[int(export_row.id)] = YoloBm1688ExportFamilyItemResponse(
            **base.model_dump(),
            model_version_id=lineage.model_version_id,
            version_no=lineage.version_no,
            dataset_version_id=lineage.dataset_version_id,
            dataset_version_no=lineage.dataset_version_no,
        )

    result = []
    for identity in selected_identities:
        grouped = sorted(
            family_rows.get(identity, []),
            key=lambda item: int(item[0].id),
            reverse=True,
        )
        if not grouped:
            continue
        _newest_export, latest_task, newest_lineage = grouped[0]
        lineages = [lineage for _row, _task, lineage in grouped]
        model_versions = {
            int(lineage.model_version_id): lineage
            for lineage in lineages
            if lineage.model_version_id is not None
        }
        latest_version = max(
            model_versions.values(),
            key=lambda lineage: (
                int(lineage.version_no or -1),
                int(lineage.model_version_id or -1),
            ),
            default=None,
        )
        latest_version_rows = (
            [
                item
                for item in grouped
                if latest_version is not None
                and item[2].model_version_id == latest_version.model_version_id
            ]
            if latest_version is not None
            else []
        )
        latest_export = (latest_version_rows or grouped)[0][0]
        # Keep the parent algorithm metadata aligned with the model version
        # that supplies latest_export.  A legacy compile row can have stale or
        # incomplete algorithm metadata while sharing the same family identity
        # as a canonical ModelVersion row.
        latest_lineage = latest_version or newest_lineage
        version_tokens = {
            (
                (
                    "model",
                    int(lineage.model_version_id),
                )
                if lineage.model_version_id is not None
                else ("training_job", int(lineage.job_id))
            )
            for lineage in lineages
        }
        grouping_sources = {lineage.grouping_source for lineage in lineages}
        grouping_source = (
            next(iter(grouping_sources)) if len(grouping_sources) == 1 else "mixed"
        )
        timestamps = [
            export_row.updated_at or export_row.created_at
            for export_row, _task, _lineage in grouped
            if export_row.updated_at or export_row.created_at
        ]
        result.append(
            YoloBm1688ExportFamilyResponse(
                family_key=identity.family_key,
                task_id=int(latest_task.id),
                task_name=latest_task.name,
                algorithm_id=latest_lineage.algorithm_id,
                algorithm_key=latest_lineage.algorithm_key,
                grouping_source=grouping_source,
                latest_model_version_id=(
                    int(latest_version.model_version_id)
                    if latest_version is not None
                    else None
                ),
                latest_version_no=(
                    int(latest_version.version_no)
                    if latest_version is not None
                    and latest_version.version_no is not None
                    else None
                ),
                version_count=len(version_tokens),
                compile_task_count=len(grouped),
                latest_export_id=int(latest_export.id),
                latest_status=str(latest_export.status or ""),
                updated_at=max(timestamps) if timestamps else None,
                exports=[
                    response_by_export_id[int(export_row.id)]
                    for export_row, _task, _lineage in grouped
                ],
            )
        )
    return result, total


def list_exports_global(
    db: Session,
    current_user: User,
    *,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    yolo_train_job_id: Optional[int] = None,
    export_id: Optional[int] = None,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    export_status: Optional[str] = None,
    page: int = 0,
    size: int = 20,
    include_summary: bool = True,
) -> Tuple[
    List[YoloBm1688ExportRowResponse],
    int,
    Optional[YoloBm1688ExportCatalogSummary],
]:
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    current_user_is_superuser = is_task_superuser(current_user)
    current_user_is_builtin_admin = is_consistent_pool_admin(current_user)
    task_ids_filter = yolo_train_service._task_ids_for_rect_preset_filter(
        db, current_user, rect_preset_slug=rect_preset_slug
    )
    rows, total = crud_yolo_bm1688_export.list_for_user(
        db,
        current_user.id,
        page=page,
        size=size,
        dataset_category_id=dataset_category_id,
        task_ids=task_ids_filter,
        task_access_unrestricted=current_user_is_superuser,
        task_access_builtin_admin=current_user_is_builtin_admin,
        export_status=export_status,
        task_name=task_name,
        search=search,
        yolo_train_job_id=yolo_train_job_id,
        export_id=export_id,
    )
    catalog_rows = rows
    summary = None
    if include_summary:
        summary = get_exports_summary_global(
            db,
            current_user,
            dataset_category_id=dataset_category_id,
            rect_preset_slug=rect_preset_slug,
            yolo_train_job_id=yolo_train_job_id,
            task_name=task_name,
            search=search,
            export_status=export_status,
            task_ids_filter=task_ids_filter,
            total=total,
            current_user_is_superuser=current_user_is_superuser,
        )
    edge_validations, jobs = _export_catalog_context(db, catalog_rows)
    policy_snapshots = _quality_policy_snapshots_for_export_rows(catalog_rows)
    strategy_tasks = crud_strategy_task_integration.get_by_related_export_ids(
        db, export_ids=(export_row.id for export_row, _ in rows)
    )
    compiler_config = cfg_io.load_bm1688_compiler_config() if rows else None
    data = [
        _export_row_to_response(
            export_row,
            task.name,
            db,
            current_user=current_user,
            edge_validation_override=edge_validations.get(int(export_row.id)),
            job_override=jobs.get(int(export_row.yolo_train_job_id)),
            task_override=task,
            quality_policy_snapshot_override=policy_snapshots.get(
                _rect_preset_slug_for_task(task)
            ),
            current_user_is_superuser=current_user_is_superuser,
            strategy_task_override=strategy_tasks.get(int(export_row.id)),
            compiler_config_override=compiler_config,
        )
        for export_row, task in rows
    ]
    if summary is None and include_summary:
        summary = _summarize_export_catalog_rows(
            catalog_rows,
            total,
            edge_validations=edge_validations,
            jobs=jobs,
            policy_snapshots=policy_snapshots,
        )
    return data, total, summary


def get_exports_summary_global(
    db: Session,
    current_user: User,
    *,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    yolo_train_job_id: Optional[int] = None,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    export_status: Optional[str] = None,
    task_ids_filter=None,
    total: Optional[int] = None,
    current_user_is_superuser: Optional[bool] = None,
) -> YoloBm1688ExportCatalogSummary:
    """计算模型中心筛选范围统计，使用摘要列投影避免加载制品大字段。"""
    if current_user_is_superuser is None:
        current_user_is_superuser = is_task_superuser(current_user)
    current_user_is_builtin_admin = is_consistent_pool_admin(current_user)
    if task_ids_filter is None:
        task_ids_filter = yolo_train_service._task_ids_for_rect_preset_filter(
            db, current_user, rect_preset_slug=rect_preset_slug
        )
    if total is None:
        _, total = crud_yolo_bm1688_export.list_for_user(
            db,
            current_user.id,
            page=0,
            size=1,
            dataset_category_id=dataset_category_id,
            task_ids=task_ids_filter,
            task_access_unrestricted=current_user_is_superuser,
            task_access_builtin_admin=current_user_is_builtin_admin,
            export_status=export_status,
            task_name=task_name,
            search=search,
            yolo_train_job_id=yolo_train_job_id,
        )
    total = int(total or 0)
    if total == 0:
        return YoloBm1688ExportCatalogSummary(total=0)

    summary_key = (
        int(current_user.id),
        bool(current_user_is_superuser),
        dataset_category_id,
        rect_preset_slug or "",
        yolo_train_job_id,
        task_name or "",
        search or "",
        export_status or "",
        int(total),
        (
            None
            if task_ids_filter is None
            else tuple(sorted(int(task_id) for task_id in task_ids_filter))
        ),
    )

    def compute_summary():
        chunk_size = min(_EXPORT_SUMMARY_CHUNK_SIZE, max(total, 1))
        aggregate = {
            "running": 0,
            "blocked": 0,
            "publish_ready": 0,
            "pending_edge": 0,
            "failed": 0,
        }
        page = 0
        processed = 0
        single_part = None
        while True:
            summary_rows, _ = crud_yolo_bm1688_export.list_for_user(
                db,
                current_user.id,
                page=page,
                size=chunk_size,
                dataset_category_id=dataset_category_id,
                task_ids=task_ids_filter,
                task_access_unrestricted=current_user_is_superuser,
                task_access_builtin_admin=current_user_is_builtin_admin,
                export_status=export_status,
                task_name=task_name,
                search=search,
                yolo_train_job_id=yolo_train_job_id,
                summary_projection=True,
                include_total=False,
            )
            if not summary_rows:
                break
            summary_edge_validations, summary_jobs = _export_catalog_context(
                db, summary_rows
            )
            summary_policies = _quality_policy_snapshots_for_export_rows(summary_rows)
            part = _summarize_export_catalog_rows(
                summary_rows,
                len(summary_rows),
                edge_validations=summary_edge_validations,
                jobs=summary_jobs,
                policy_snapshots=summary_policies,
            )
            single_part = part if processed == 0 else None
            for key in aggregate:
                aggregate[key] += int(getattr(part, key, 0) or 0)
            processed += len(summary_rows)
            if len(summary_rows) < chunk_size or processed >= total:
                break
            page += 1
        if single_part is not None and processed <= chunk_size:
            return single_part
        return YoloBm1688ExportCatalogSummary(total=total, **aggregate)

    return _run_export_summary_singleflight(summary_key, compute_summary)


def _export_catalog_context(db: Session, rows) -> tuple[dict[int, Any], dict[int, Any]]:
    export_rows = [export_row for export_row, _ in rows]
    from labelu.internal.application.service import (
        yolo_aicm_publish as yolo_aicm_publish_service,
    )

    edge_validations = yolo_aicm_publish_service.edge_validation_statuses_for_exports(
        db, export_rows
    )
    job_ids = {
        int(export_row.yolo_train_job_id)
        for export_row in export_rows
        if getattr(export_row, "yolo_train_job_id", None)
    }
    job_rows = (
        db.query(YoloTrainJob).filter(YoloTrainJob.id.in_(job_ids)).all()
        if job_ids
        else []
    )
    return edge_validations, {int(job.id): job for job in job_rows}


def _rect_preset_slug_for_task(task: Any) -> str:
    task_cfg = yolo_train_service._parse_task_config(getattr(task, "config", None))
    return str(task_cfg.get("defaultRectPresetId") or "").strip()


def _quality_policy_snapshots_for_export_rows(rows) -> dict[str, dict[str, Any]]:
    from labelu.internal.application.service.yolo_quality_policy import (
        resolve_policy_for_preset,
    )

    snapshots: dict[str, dict[str, Any]] = {}
    for _, task in rows:
        slug = _rect_preset_slug_for_task(task)
        if slug not in snapshots:
            snapshots[slug] = resolve_policy_for_preset(slug or None) or {
                "_catalog_default": True
            }
    return snapshots


def _summarize_export_catalog_rows(
    rows,
    total: int,
    *,
    edge_validations: dict[int, Any],
    jobs: dict[int, Any],
    policy_snapshots: Optional[dict[str, dict[str, Any]]] = None,
) -> YoloBm1688ExportCatalogSummary:
    from labelu.internal.application.service.yolo_quality_assessment import (
        build_assessment_payload,
        quality_policy_snapshot_for_job,
    )

    running = blocked = publish_ready = pending_edge = failed = 0
    terminal_edge_statuses = {"COMPLETED", "PARTIAL_COMPLETED", "FAILED"}
    for export_row, task in rows:
        export_status = str(export_row.status or "").upper()
        edge_validation = edge_validations.get(int(export_row.id))
        board_status = str(getattr(edge_validation, "status", "") or "").upper()
        job = jobs.get(int(export_row.yolo_train_job_id))
        release_status = ""
        gate_label = "—"
        if job is not None:
            task_cfg = yolo_train_service._parse_task_config(task.config)
            task_rect_preset_slug = (
                str(task_cfg.get("defaultRectPresetId") or "").strip() or None
            )
            assessment = build_assessment_payload(
                training_val_metrics=yolo_train_service._training_val_metrics_for_job(
                    job
                ),
                training_assessment_override=(
                    yolo_train_service._composite_training_assessment_for_job(job)
                ),
                test_val_metrics=yolo_train_service._test_val_metrics_for_job(job),
                val_test_comparison=yolo_train_service._val_test_comparison_for_job(
                    job
                ),
                total_sample_count=yolo_train_service._sample_counts_for_job(job).get(
                    "total"
                ),
                edge_validation=edge_validation,
                rect_preset_slug=task_rect_preset_slug,
                pt_to_bmodel_comparison=_effective_pt_bmodel_comparison(
                    export_row, edge_validation
                ),
                pt_to_bmodel_comparison_expected=True,
                quality_policy_snapshot=(
                    quality_policy_snapshot_for_job(job)
                    or (policy_snapshots or {}).get(_rect_preset_slug_for_task(task))
                ),
            )
            gate_label = str(assessment.training_assessment.status_label or "—")
            release_status = str(
                assessment.release_gate_assessment.status_code or ""
            ).lower()
        verdict_code = _verdict_code(export_row, gate_label, edge_validation)
        if export_status in {"PENDING", "PROCESSING"} or board_status in {
            "PENDING",
            "PROCESSING",
        }:
            running += 1
        if export_status == "COMPLETED" and board_status not in terminal_edge_statuses:
            pending_edge += 1
        if (
            release_status == "blocked"
            or verdict_code.endswith("_failed")
            or export_status == "FAILED"
        ):
            blocked += 1
        if (
            release_status in {"ready", "publishable", "passed"}
            or verdict_code == "release_ready"
        ):
            publish_ready += 1
        if export_status == "FAILED" or board_status == "FAILED":
            failed += 1
    return YoloBm1688ExportCatalogSummary(
        total=int(total),
        running=running,
        blocked=blocked,
        publish_ready=publish_ready,
        pending_edge=pending_edge,
        failed=failed,
    )


def _summarize_export_catalog(
    rows: List[YoloBm1688ExportRowResponse], total: int
) -> YoloBm1688ExportCatalogSummary:
    running = 0
    blocked = 0
    publish_ready = 0
    pending_edge = 0
    failed = 0
    terminal_edge_statuses = {"COMPLETED", "PARTIAL_COMPLETED", "FAILED"}
    for row in rows:
        export_status = str(row.status or "").upper()
        board_status = str(
            row.board_validation.status if row.board_validation else ""
        ).upper()
        release_status = str(
            row.release_gate_assessment.status_code
            if row.release_gate_assessment
            else ""
        ).lower()
        verdict = str(row.current_verdict_label or "")
        verdict_code = str(row.verdict_code or "").lower()
        if export_status in {"PENDING", "PROCESSING"} or board_status in {
            "PENDING",
            "PROCESSING",
        }:
            running += 1
        if export_status == "COMPLETED" and board_status not in terminal_edge_statuses:
            pending_edge += 1
        if (
            release_status == "blocked"
            or verdict_code.endswith("_failed")
            or export_status == "FAILED"
        ):
            blocked += 1
        if (
            release_status in {"ready", "publishable", "passed"}
            or "可进入发布评估" in verdict
            or "可发布" in verdict
        ):
            publish_ready += 1
        if export_status == "FAILED" or board_status == "FAILED":
            failed += 1
    return YoloBm1688ExportCatalogSummary(
        total=int(total),
        running=running,
        blocked=blocked,
        publish_ready=publish_ready,
        pending_edge=pending_edge,
        failed=failed,
    )


def resolve_bm1688_media_file(rel_stored: str) -> Path:
    if not rel_stored or not rel_stored.strip():
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    p = Path(rel_stored.strip())
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / p
    resolved = p.resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    if not str(resolved).startswith(str(root)):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not resolved.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return resolved


def is_bmodel_artifact_ready(row) -> bool:
    """Return whether an export has a bmodel usable by downstream workflows.

    ``ONNX_READY`` is a valid partial export state when Docker has produced
    the INT8 artifact but optional host-side FP32/FP16 compilation is not
    available. Downstream strategy and publish flows only need a real bmodel.
    """
    export_status = str(getattr(row, "status", "") or "").strip().upper()
    if export_status == "COMPLETED":
        return True
    if export_status != "ONNX_READY":
        return False
    bmodel_rel_path = str(getattr(row, "bmodel_rel_path", "") or "").strip()
    if not bmodel_rel_path:
        return False
    try:
        resolve_bm1688_media_file(bmodel_rel_path)
    except (LabelUException, OSError, TypeError):
        return False
    return True


def is_bmodel_release_ready(row, *, required_precisions=None) -> bool:
    """Return whether the export has the complete release artifact bundle.

    ``is_bmodel_artifact_ready`` intentionally remains a lightweight downstream
    readiness check for historical/partial exports.  Publication is stricter:
    an ONNX-only or single-precision export must never be treated as a release
    candidate.  Specialized plate jobs may pass ``("fp16",)`` because their
    runtime contract deliberately contains one FP16 model.
    """
    if str(getattr(row, "status", "") or "").strip().upper() != "COMPLETED":
        return False
    precisions = tuple(required_precisions or ("fp32", "fp16", "int8"))
    if not precisions:
        return False
    names = _list_bmodel_names(int(getattr(row, "id", 0) or 0))
    available = set()
    for name in names:
        filename = Path(name).name.lower()
        for precision in ("fp32", "fp16", "int8"):
            if f"_{precision}_" in filename or filename.endswith(
                f"_{precision}_1b.bmodel"
            ):
                available.add(precision)
    return all(precision in available for precision in precisions)


def get_export_for_download(
    db: Session,
    *,
    export_id: int,
    current_user: User,
):
    row = crud_yolo_bm1688_export.get(db, export_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = crud_task.get(db=db, task_id=row.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    return row


def get_sop_eval_report_markdown(
    db: Session,
    *,
    export_id: int,
    kind: str,
    current_user: User,
) -> str:
    """文档 3.6.2：返回 server_eval 下 evaluation_report.md 正文。"""
    get_export_for_download(db, export_id=export_id, current_user=current_user)
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        resolve_sop_eval_report_path,
    )

    try:
        path = resolve_sop_eval_report_path(export_id, kind)
    except FileNotFoundError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=(
                str(exc) if is_task_superuser(current_user) else "评估报告尚未生成"
            ),
        ) from exc
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc) or "评估报告尚未生成",
        ) from exc
    return path.read_text(encoding="utf-8", errors="replace")


def get_sop_eval_asset_path(
    db: Session,
    *,
    export_id: int,
    kind: str,
    filename: str,
    current_user: User,
) -> Path:
    get_export_for_download(db, export_id=export_id, current_user=current_user)
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        resolve_sop_eval_asset_path,
    )

    try:
        return resolve_sop_eval_asset_path(export_id, kind, filename)
    except FileNotFoundError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=(str(exc) if is_task_superuser(current_user) else "评估资源不存在"),
        ) from exc
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc) or "评估资源不存在",
        ) from exc


def get_runtime_log_text(
    db: Session,
    *,
    export_id: int,
    kind: str,
    current_user: User,
) -> str:
    """返回导出工作区的完整运行日志正文。"""
    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    kind_value = str(kind or "").strip().lower()

    if kind_value in ("compile", "quantize"):
        from labelu.internal.common.pipeline_process_log import (
            bm1688_compile_log_path,
            bm1688_quantize_log_path,
        )

        path = (
            bm1688_compile_log_path(row.id)
            if kind_value == "compile"
            else bm1688_quantize_log_path(row.id)
        )
    elif kind_value == "edge_remote_run":
        from labelu.internal.application.service.yolo_edge_validation_pack import (
            edge_validation_job_root,
        )

        latest_job = crud_yolo_edge_validation_job.get_latest_for_export(
            db, export_id=row.id
        )
        if not latest_job:
            raise LabelUException(
                code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
                status_code=status.HTTP_404_NOT_FOUND,
                message="当前导出还没有边端执行日志",
            )
        path = (
            edge_validation_job_root(export_id=row.id, job_id=int(latest_job.id))
            / "edge_ssh_remote_run.log"
        )
    else:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="不支持的运行日志类型",
        )

    if not path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="运行日志尚未生成",
        )
    return path.read_text(encoding="utf-8", errors="replace")


def _resolve_onnx_path_for_row(row) -> Path:
    if row.onnx_rel_path:
        return resolve_bm1688_media_file(row.onnx_rel_path)
    out_root = _export_dir_for_row(row.id)
    file_cfg = cfg_io.load_bm1688_compiler_config()
    onnx_fn = str(file_cfg.get("onnx_filename") or cfg_io.DEFAULT_ONNX_FILENAME)
    dest = cfg_io.sophon_onnx_dir(out_root) / onnx_fn
    if dest.is_file():
        return dest.resolve()
    raise LabelUException(
        code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
        status_code=status.HTTP_404_NOT_FOUND,
        message="ONNX 未生成，无法编译 bmodel",
    )


def _can_recompile_bmodel(row, *, db: Session | None = None) -> bool:
    try:
        _resolve_onnx_path_for_row(row)
        return True
    except LabelUException:
        # ONNX 丢失时仍允许用训练作业的 best.pt 重新走完整导出，
        # 这样“重新编译”可以修复不完整的自动编译结果。
        try:
            if db is not None:
                job = crud_yolo_train_job.get(db, int(row.yolo_train_job_id))
                return bool(
                    job
                    and (job.weights_path or "").strip()
                    and resolve_weights_path(job.weights_path).is_file()
                )
            from labelu.internal.common.db import session_scope

            with session_scope() as read_db:
                job = crud_yolo_train_job.get(read_db, int(row.yolo_train_job_id))
                return bool(
                    job
                    and (job.weights_path or "").strip()
                    and resolve_weights_path(job.weights_path).is_file()
                )
        except Exception:
            return False


def _export_dir_display_for_user(export_id: int, current_user: User | None) -> str:
    root = _export_dir_for_row(export_id)
    if current_user and is_task_superuser(current_user):
        return str(root)
    try:
        rel = root.resolve().relative_to(Path(settings.MEDIA_ROOT).resolve())
        return f"yolo_bm1688_exports/{rel}"
    except ValueError:
        return f"yolo_bm1688_exports/{export_id}"


_RESTRICTED_EXPORT_PATH_KEYS = {
    "path",
    "dataset_root",
    "export_root",
    "directory",
    "export_root_abs",
    "export_dir_abs",
    "images_dir",
    "xml_dir",
    "results_dir",
    "remote_base",
    "remote_bundle",
    "remote_job_dir",
    "remote_host",
    "remote_user",
    "local_bundle",
    "source_path",
    "output_dir",
    "work_dir",
    "model_dir",
    "models_dir",
    "calibration_dir",
}

_ABSOLUTE_EXPORT_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'<>]+|"
    r"\\\\[^\\/\s]+[\\/][^\s\"'<>]+|"
    r"(?<![A-Za-z0-9_:/])/(?:data|tmp|home|opt|var|mnt|root|usr|workspace|app|srv|media)"
    r"(?:/[^\s\"'<>]*)?)",
    re.IGNORECASE,
)


def _is_restricted_export_key(key: Any) -> bool:
    """Identify filesystem/deployment fields that must stay admin-only."""
    normalized = str(key or "").strip().lower().replace("-", "_")
    if not normalized or normalized == "relative_path":
        return False
    if normalized in _RESTRICTED_EXPORT_PATH_KEYS:
        return True
    return normalized.endswith(
        ("_abs", "_path", "_root", "_directory", "_dir", "_host")
    )


def _redact_export_paths(value: Any) -> Any:
    """Keep useful manifest metadata while removing machine-local paths."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized = str(key or "").strip().lower().replace("-", "_")
            if normalized == "pack_image_path":
                # Replay needs to locate the already-packed image after the
                # source attachment is cleaned up, but callers must not learn
                # the host's absolute filesystem path.
                try:
                    raw_path = Path(str(item or "")).expanduser()
                    media_root = Path(settings.MEDIA_ROOT).resolve()
                    candidate = (
                        raw_path.resolve()
                        if raw_path.is_absolute()
                        else (media_root / raw_path).resolve()
                    )
                    relative = candidate.relative_to(media_root)
                    redacted[key] = relative.as_posix()
                except (OSError, ValueError, TypeError):
                    redacted[key] = None
            elif _is_restricted_export_key(key):
                redacted[key] = None
            else:
                redacted[key] = _redact_export_paths(item)
        return redacted
    if isinstance(value, list):
        return [_redact_export_paths(item) for item in value]
    if isinstance(value, str):
        return _ABSOLUTE_EXPORT_PATH_RE.sub("[路径已隐藏]", value)
    return value


def _export_payload_for_user(
    value: Any,
    current_user: User | None,
    *,
    is_superuser: Optional[bool] = None,
) -> Any:
    allowed = (
        bool(is_superuser)
        if is_superuser is not None
        else bool(current_user and is_task_superuser(current_user))
    )
    if allowed:
        return value
    return _redact_export_paths(value)


def _redact_path_response_model_for_user(
    response: Any,
    current_user: User | None,
    *,
    empty_path_fields: tuple[str, ...] = (),
) -> Any:
    """Redact path-bearing response models without violating string fields.

    Compiler and SSH configuration responses intentionally expose path fields
    as non-optional strings for the frontend form.  The generic payload
    redactor uses ``None`` for path keys, so normalize those fields to an empty
    string after redaction to keep the response model contract stable.
    """
    if response is None or (
        current_user is not None and is_task_superuser(current_user)
    ):
        return response
    payload = (
        response.model_dump() if hasattr(response, "model_dump") else response.dict()
    )
    redacted = _export_payload_for_user(payload, current_user)
    if not isinstance(redacted, dict):
        return response
    for field_name in empty_path_fields:
        if field_name in redacted:
            redacted[field_name] = ""
    return response.__class__(**redacted)


def _redact_export_response_model_for_user(
    response: Any,
    current_user: User | None,
) -> Any:
    """Rebuild a Pydantic response after applying export path redaction."""
    if response is None or (
        current_user is not None and is_task_superuser(current_user)
    ):
        return response
    payload = (
        response.model_dump() if hasattr(response, "model_dump") else response.dict()
    )
    redacted = _export_payload_for_user(payload, current_user)
    if not isinstance(redacted, dict):
        return response
    return response.__class__(**redacted)


def _redact_board_validation_status_for_user(
    status_response: BoardValidationStatusResponse | None,
    current_user: User | None,
    *,
    is_superuser: Optional[bool] = None,
) -> BoardValidationStatusResponse | None:
    """Remove machine-local paths from board-validation responses for members.

    Board-agent and manual-validation status objects share the same nested
    ``report``/``progress`` shape.  Keeping this at the export API boundary
    prevents internal orchestration callers from losing paths they may need
    while ensuring every user-facing export endpoint applies one policy.
    """
    allowed = (
        bool(is_superuser)
        if is_superuser is not None
        else bool(current_user is not None and is_task_superuser(current_user))
    )
    if status_response is None or allowed:
        return status_response

    status_response.execution_device_host = None
    status_response.threshold_contract = _export_payload_for_user(
        status_response.threshold_contract,
        current_user,
        is_superuser=allowed,
    )
    status_response.report = _export_payload_for_user(
        status_response.report,
        current_user,
        is_superuser=allowed,
    )
    status_response.train_board_comparison = _export_payload_for_user(
        status_response.train_board_comparison,
        current_user,
        is_superuser=allowed,
    )
    for field_name in ("error_message", "report_summary"):
        field_value = getattr(status_response, field_name, None)
        if isinstance(field_value, str):
            setattr(status_response, field_name, _redact_export_paths(field_value))
    if status_response.progress is not None:
        progress_model = status_response.progress
        progress_payload_raw = (
            progress_model.model_dump()
            if hasattr(progress_model, "model_dump")
            else progress_model.dict()
        )
        progress_payload = _export_payload_for_user(progress_payload_raw, current_user)
        status_response.progress = BoardValidationProgressResponse(
            **(progress_payload if isinstance(progress_payload, dict) else {})
        )
    return status_response


def _export_detail_response(
    row,
    task_name: Optional[str],
    db: Session,
    *,
    current_user: User | None = None,
    compact_manifest: bool = False,
) -> YoloBm1688ExportDetailResponse:
    from labelu.internal.common.bm1688_quality_report import (
        hydrate_quality_report_from_disk,
        parse_quality_report,
    )

    from labelu.internal.common.bm1688_quality_refresh import read_refresh_status

    base = _export_row_to_response(
        row,
        task_name,
        db,
        include_board_report=True,
        current_user=current_user,
    )
    qr = hydrate_quality_report_from_disk(
        row.id,
        parse_quality_report(getattr(row, "quality_report", None)),
        base.board_validation,
    )
    base_payload = base.dict()
    if compact_manifest:
        base_payload["artifact_manifest"] = _compact_artifact_manifest(
            base_payload.get("artifact_manifest")
        )
    # 详情只处理单条记录，沿用单参数调用也兼容外部扩展对该判断函数的替换。
    base_payload["can_recompile_bmodel"] = _can_recompile_bmodel(row)
    return YoloBm1688ExportDetailResponse(
        **base_payload,
        compile_log=_export_payload_for_user(row.compile_log, current_user),
        export_dir=_export_dir_display_for_user(row.id, current_user),
        export_dir_abs=(
            str(_export_dir_for_row(row.id).resolve())
            if current_user and is_task_superuser(current_user)
            else ""
        ),
        quality_report=_export_payload_for_user(qr, current_user),
        quality_report_refresh=_export_payload_for_user(
            read_refresh_status(row.id), current_user
        ),
    )


def _export_summary_response(
    row,
    task_name: Optional[str],
    db: Session,
    *,
    current_user: User | None = None,
) -> YoloBm1688ExportDetailResponse:
    from labelu.internal.common.bm1688_quality_refresh import read_refresh_status

    base = _export_row_to_response(
        row,
        task_name,
        db,
        include_board_report=False,
        current_user=current_user,
    )
    base_payload = base.dict()
    base_payload["can_recompile_bmodel"] = _can_recompile_bmodel(row)
    return YoloBm1688ExportDetailResponse(
        **base_payload,
        compile_log=_export_payload_for_user(row.compile_log, current_user),
        export_dir=_export_dir_display_for_user(row.id, current_user),
        export_dir_abs=(
            str(_export_dir_for_row(row.id).resolve())
            if current_user and is_task_superuser(current_user)
            else ""
        ),
        quality_report=None,
        quality_report_refresh=_export_payload_for_user(
            read_refresh_status(row.id), current_user
        ),
    )


async def refresh_export_quality_report(
    db: Session,
    *,
    export_id: int,
    current_user: User,
    run_onnx_val: bool = False,
) -> YoloBm1688ExportDetailResponse:
    row = crud_yolo_bm1688_export.get(db, export_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = crud_task.get(db=db, task_id=row.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    from labelu.internal.common.bm1688_quality_refresh import (
        is_refresh_running,
        start_quality_report_refresh_background,
    )

    if is_refresh_running(export_id):
        raise LabelUException(
            code=ErrorCode.CODE_57032_EDGE_SSH_VALIDATION_BUSY,
            status_code=status.HTTP_409_CONFLICT,
            message="质量报告 / SOP 评估正在后台刷新，请稍后轮询详情",
        )
    start_quality_report_refresh_background(export_id, run_onnx_val=run_onnx_val)
    fresh = crud_yolo_bm1688_export.get(db, export_id)
    task = crud_task.get(db=db, task_id=fresh.task_id)
    return _export_summary_response(
        fresh, task.name if task else None, db, current_user=current_user
    )


def _recompile_cli_summary(export_id: int) -> dict:
    env_file = (os.environ.get("LABELU_ENV_FILE") or "").strip()
    out_root = _export_dir_for_row(export_id)
    bmodels = _list_bmodel_names(export_id)
    return {
        "export_id": export_id,
        "base_data_dir": str(settings.BASE_DATA_DIR),
        "media_root": str(settings.MEDIA_ROOT),
        "labelu_env_file": env_file,
        "export_dir": str(out_root),
        "bmodel_files": bmodels,
    }


def format_recompile_cli_report(summary: dict, *, log_tail_lines: int = 40) -> str:
    lines = [
        f"export_id={summary.get('export_id')}",
        f"LABELU_ENV_FILE={summary.get('labelu_env_file') or '(未加载 .env，可能用了默认 ~/.local/share/labelu)'}",
        f"BASE_DATA_DIR={summary.get('base_data_dir')}",
        f"MEDIA_ROOT={summary.get('media_root')}",
        f"export_dir={summary.get('export_dir')}",
        f"status={summary.get('status') or '(无记录)'}",
    ]
    err = summary.get("error_message")
    if err:
        lines.append(f"error_message={err}")
    if "retryable" in summary:
        lines.append(f"retryable={str(bool(summary.get('retryable'))).lower()}")
    bmodels = summary.get("bmodel_files") or []
    if bmodels:
        lines.append("bmodel_files:")
        lines.extend(f"  - {n}" for n in bmodels)
    else:
        lines.append("bmodel_files: (无)")
    bmodels = summary.get("bmodel_files") or []
    missing = []
    if summary.get("status") != "COMPLETED":
        for tag in ("fp32", "fp16", "int8"):
            if not any(f"_{tag}_" in b for b in bmodels):
                missing.append(f"*_{tag}_1b.bmodel")
    if missing:
        lines.append("missing_bmodels: " + ", ".join(missing))
        lines.append(
            "补 int8: bash labelu/scripts/bm1688_onnx_to_bmodel.sh <onnx> <export_dir> "
            "640 bm1688 int8_only <cali> <qtable> 1 yolov11n bm1688"
        )
    tail = (summary.get("compile_log_tail") or "").strip()
    if tail:
        lines.append(f"--- compile_log (last {log_tail_lines} lines) ---")
        lines.append(tail)
    return "\n".join(lines)


def _recompile_retryable_error_message(error: BaseException) -> str:
    """Return a user-facing failure reason for a recompile that can be retried.

    Recompile has no separate retryable status in the legacy export table.  The
    terminal ``FAILED`` state plus this explicit marker lets the UI distinguish
    a failed attempt from an export that is still waiting for a worker.
    """

    detail = str(error).strip() or error.__class__.__name__
    return f"BM1688 重新编译失败（可重试）：{detail[:7900]}"


def _persist_recompile_failure(
    export_id: int,
    error_message: str,
    *,
    execution_monitor: Optional[ExecutionLeaseMonitor],
) -> str | None:
    """Persist a recompile failure without allowing a stale worker to overwrite.

    The dedicated Worker always supplies ``execution_monitor`` after its
    readiness gate.  The no-monitor branch exists only for the historical
    compatibility path during migration; production Workers fail closed before
    reaching it.
    """

    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        row = crud_yolo_bm1688_export.get(db, int(export_id))
        if row is None:
            return None
        updates = {"status": "FAILED", "error_message": error_message[:8000]}
        if execution_monitor is not None:
            execution_monitor.conditional_update(
                updates,
                db=db,
                manage_transaction=True,
            )
        else:
            with begin_transaction(db):
                crud_yolo_bm1688_export.update_row(db, row, **updates)
        return "FAILED"


def _run_bm1688_recompile_sync(export_id: int) -> dict:
    from labelu.internal.common.bm1688_deploy_bootstrap import (
        ensure_bm1688_compiler_ready,
    )
    from labelu.internal.common.db import session_scope

    summary = _recompile_cli_summary(export_id)
    summary["found"] = False
    summary["status"] = None
    summary["error_message"] = None
    summary["compile_log_tail"] = ""

    with session_scope() as dispatch_db:
        # 这里只读取路由所需的两列，迁移窗口中旧库可能尚未有新动作字段。
        dispatch_row = (
            dispatch_db.query(
                YoloBm1688Export.id,
                YoloBm1688Export.yolo_train_job_id,
            )
            .filter(YoloBm1688Export.id == int(export_id))
            .first()
        )
        dispatch_job = (
            crud_yolo_train_job.get(dispatch_db, dispatch_row.yolo_train_job_id)
            if dispatch_row
            else None
        )
        dispatch_job_type = str(getattr(dispatch_job, "job_type", None) or "").strip()
        if dispatch_job_type == "plate_detection":
            from labelu.internal.application.service.plate_detection_bm1688 import (
                run_plate_detection_bm1688_export,
            )

            run_plate_detection_bm1688_export(export_id)
            refreshed = _recompile_cli_summary(export_id)
            refreshed["found"] = True
            return refreshed
        if dispatch_job_type == "plate_recognition":
            from labelu.internal.application.service.plate_recognition_bm1688 import (
                run_plate_recognition_bm1688_export,
            )

            run_plate_recognition_bm1688_export(export_id)
            refreshed = _recompile_cli_summary(export_id)
            refreshed["found"] = True
            return refreshed

    # 手动重新编译的输入始终是当前训练作业的 best.pt。若历史自动任务只
    # 完成了部分步骤、尚未留下 ONNX，则退回完整 PT→ONNX→BModel 链路。
    try:
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, int(export_id))
            job = (
                crud_yolo_train_job.get(db, int(row.yolo_train_job_id)) if row else None
            )
            if row:
                _resolve_onnx_path_for_row(row)
            if row and job and (job.weights_path or "").strip():
                resolve_weights_path(job.weights_path)
    except LabelUException:
        try:
            _run_bm1688_export_sync(int(export_id))
            refreshed = _recompile_cli_summary(export_id)
            refreshed["found"] = True
            return refreshed
        except Exception:
            raise

    execution_guard = _acquire_export_execution_guard(export_id)
    if execution_guard is None:
        summary["found"] = True
        summary["status"] = "PROCESSING"
        summary["error_message"] = "该模型编译任务正在执行，已跳过重复编译"
        logger.warning("skip duplicate BM1688 recompile export_id={}", export_id)
        return summary

    execution_monitor = None
    fencing_unavailable = False
    try:
        execution_monitor, fencing_unavailable, already_claimed = (
            _start_export_execution_monitor(export_id, label="bm1688-recompile")
        )
        if already_claimed:
            summary["found"] = True
            summary["status"] = "PROCESSING"
            summary["error_message"] = "该模型编译任务已被其他执行器领取"
            logger.warning(
                "skip BM1688 recompile claimed by another worker export_id={}",
                export_id,
            )
            _stop_export_execution_monitor(execution_monitor)
            execution_guard.release()
            return summary
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        ensure_bm1688_compiler_ready()
    except ExecutionFencingLost:
        _stop_export_execution_monitor(execution_monitor)
        execution_guard.release()
        raise
    except Exception as exc:
        retryable_message = _recompile_retryable_error_message(exc)
        # A dedicated Worker has a monitor; a missing-column migration window
        # may use the explicit legacy compatibility path.  Do not write a
        # terminal state when lease acquisition itself failed for another
        # reason, because ownership could not be established.
        if execution_monitor is not None or fencing_unavailable:
            try:
                _persist_recompile_failure(
                    export_id,
                    retryable_message,
                    execution_monitor=execution_monitor,
                )
            except ExecutionFencingLost:
                logger.warning(
                    "skip stale BM1688 recompile setup failure export_id={}",
                    export_id,
                )
            except Exception:
                logger.exception(
                    "persist BM1688 recompile setup failure failed export_id={}",
                    export_id,
                )
        summary["found"] = True
        summary["status"] = "FAILED"
        summary["retryable"] = True
        summary["error_message"] = retryable_message
        _stop_export_execution_monitor(execution_monitor)
        execution_guard.release()
        raise
    try:
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            if not row:
                summary["error_message"] = (
                    f"数据库中无 export_id={export_id}（请确认 BASE_DATA_DIR 与 systemd 一致）"
                )
                return summary
            summary["found"] = True
            summary["status"] = row.status
            summary["error_message"] = row.error_message
            try:
                dest_onnx = _resolve_onnx_path_for_row(row)
            except LabelUException:
                missing_onnx_message = (
                    "BM1688 重新编译阻断：ONNX 不存在，补齐 ONNX 后可重试"
                )
                if execution_monitor is not None:
                    execution_monitor.conditional_update(
                        {
                            "status": "FAILED",
                            "error_message": missing_onnx_message,
                        },
                        db=db,
                        manage_transaction=True,
                    )
                else:
                    with begin_transaction(db):
                        crud_yolo_bm1688_export.update_row(
                            db,
                            row,
                            status="FAILED",
                            error_message=missing_onnx_message,
                        )
                row = crud_yolo_bm1688_export.get(db, export_id)
                summary["status"] = row.status if row else "FAILED"
                summary["error_message"] = (
                    row.error_message if row else summary["error_message"]
                )
                return summary

            out_root = _export_dir_for_row(export_id)
            imgsz = int(row.imgsz or 640)
            prev_log = (row.compile_log or "").strip()
            export_task_id = int(row.task_id)
            export_train_job_id = int(row.yolo_train_job_id)
            job = crud_yolo_train_job.get(db, export_train_job_id)
            recompile_weights = job.model_weights if job else None

            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    {"status": "PROCESSING", "error_message": None},
                    db=db,
                    manage_transaction=True,
                )
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(
                        db, row, status="PROCESSING", error_message=None
                    )
            seed_lines = (
                (prev_log + "\n--- recompile ---").splitlines() if prev_log else []
            )
            seed_lines.append(f"recompile_start={datetime.now().isoformat()}")
            _persist_export_progress(
                export_id,
                seed_lines,
                status="PROCESSING",
                error_message=None,
                execution_monitor=execution_monitor,
            )

        compile_w, quantize_w = _open_bm1688_process_log_writers(export_id)
        compile_w.write("phase=recompile")
        compile_w.write(f"process_log_compile={compile_w.path}")
        quantize_w.write(f"process_log_quantize={quantize_w.path}")
        try:
            final_status, bmodel_rel, compile_log, compile_err = (
                _run_bmodel_compile_phase(
                    export_id,
                    dest_onnx,
                    out_root,
                    imgsz,
                    yolo_train_job_id=export_train_job_id,
                    model_weights=recompile_weights,
                    compile_writer=compile_w,
                    quantize_writer=quantize_w,
                )
            )
        finally:
            compile_w.close()
            quantize_w.close()
        if prev_log:
            compile_log = f"{prev_log}\n--- recompile ---\n{compile_log}"[:12000]

        if execution_monitor is not None:
            execution_monitor.ensure_current()
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            if not row:
                return summary
            final_updates = {
                "status": final_status,
                "bmodel_rel_path": bmodel_rel,
                "compile_log": compile_log or None,
                "error_message": (
                    None
                    if final_status == "COMPLETED"
                    else (compile_err or row.error_message)
                ),
            }
            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    final_updates, db=db, manage_transaction=True
                )
                db.expire(row)
                db.refresh(row)
                _persist_artifact_manifest(db, row, execution_monitor=execution_monitor)
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(db, row, **final_updates)
                    _persist_artifact_manifest(db, row)
            if final_status == "COMPLETED":
                _sync_model_version_artifacts_for_export(db, row)
            summary["status"] = row.status
            summary["error_message"] = row.error_message
            log_text = (row.compile_log or "").strip()
            if log_text:
                summary["compile_log_tail"] = "\n".join(log_text.splitlines()[-40:])
        if final_status == "COMPLETED":
            try:
                from labelu.internal.application.service.yolo_sop_model_evaluation import (
                    run_server_sop_evaluations_for_export,
                )

                run_server_sop_evaluations_for_export(
                    export_id,
                    yolo_train_job_id=export_train_job_id,
                    imgsz=imgsz,
                    execution_monitor=execution_monitor,
                )
            except ExecutionFencingLost:
                raise
            except Exception:
                logger.exception(
                    "SOP server eval failed after recompile export_id={}",
                    export_id,
                )
            _maybe_run_edge_ssh_validation_sync(
                export_id,
                yolo_train_job_id=export_train_job_id,
                imgsz=imgsz,
                force_run=True,
                execution_monitor=execution_monitor,
            )
        summary.update(_recompile_cli_summary(export_id))
        summary["found"] = True
        _generate_quality_report_sync(
            export_id,
            run_onnx_val=False,
            execution_monitor=execution_monitor,
        )
    except ExecutionFencingLost as e:
        logger.error(
            "BM1688 recompile fencing lost export_id={} error={}", export_id, e
        )
        summary["error_message"] = str(e)[:8000]
    except Exception as e:
        logger.exception("yolo bm1688 recompile failed export_id={}", export_id)
        retryable_message = _recompile_retryable_error_message(e)
        summary["error_message"] = retryable_message
        summary["retryable"] = True
        if execution_monitor is not None or fencing_unavailable:
            try:
                summary["status"] = (
                    _persist_recompile_failure(
                        export_id,
                        retryable_message,
                        execution_monitor=execution_monitor,
                    )
                    or "FAILED"
                )
            except ExecutionFencingLost:
                logger.warning(
                    "skip stale BM1688 recompile failure export_id={}", export_id
                )
                summary["retryable"] = False
            except Exception:
                logger.exception("bm1688 recompile persist failure")
        else:
            logger.warning(
                "skip BM1688 recompile failure without execution ownership "
                "export_id={}",
                export_id,
            )
            summary["retryable"] = False
    finally:
        _stop_export_execution_monitor(execution_monitor)
        execution_guard.release()
    return summary


def verify_export_sophon_sync(export_id: int) -> dict:
    from labelu.internal.common import sophon_faq_verify

    out_root = _export_dir_for_row(export_id)
    report = sophon_faq_verify.verify_export_tree(out_root, check_env=True)
    return report.to_dict()


async def get_export_sophon_verify(
    db: Session,
    *,
    export_id: int,
    current_user: User,
) -> dict:
    get_export_for_download(db, export_id=export_id, current_user=current_user)
    loop = asyncio.get_event_loop()
    report = await loop.run_in_executor(None, verify_export_sophon_sync, export_id)
    return _export_payload_for_user(report, current_user)


async def get_compiler_sophon_verify() -> dict:
    from labelu.internal.common import sophon_faq_verify

    report = sophon_faq_verify.SophonVerifyReport()
    sophon_faq_verify.verify_compiler_config(report)
    return report.to_dict()


async def get_export_detail(
    db: Session,
    *,
    export_id: int,
    current_user: User,
) -> YoloBm1688ExportDetailResponse:
    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    task = crud_task.get(db=db, task_id=row.task_id)
    return _export_detail_response(
        row, task.name if task else None, db, current_user=current_user
    )


async def get_export_summary(
    db: Session,
    *,
    export_id: int,
    current_user: User,
) -> YoloBm1688ExportDetailResponse:
    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    task = crud_task.get(db=db, task_id=row.task_id)
    return _export_summary_response(
        row, task.name if task else None, db, current_user=current_user
    )


async def get_export_evaluation(
    db: Session,
    *,
    export_id: int,
    current_user: User,
) -> YoloBm1688ExportDetailResponse:
    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    task = crud_task.get(db=db, task_id=row.task_id)
    return _export_detail_response(
        row,
        task.name if task else None,
        db,
        current_user=current_user,
        compact_manifest=True,
    )


async def start_recompile_for_export(
    db: Session,
    *,
    export_id: int,
    current_user: User,
) -> YoloBm1688ExportDetailResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    if not _can_recompile_bmodel(row):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="ONNX 未就绪，无法编译 bmodel",
        )

    current_is_running = str(getattr(row, "status", "") or "").upper() in {
        "PENDING",
        "PROCESSING",
    }
    from labelu.internal.common.execution_fencing import execution_lease_is_active

    running_lease = execution_lease_is_active(row, now=datetime.now())
    with begin_transaction(db):
        current = crud_yolo_bm1688_export.get(db, export_id)
        if current is not None:
            # 保留 PROCESSING 状态表示旧自动编译仍在安全收尾；等待线程会
            # 在租约释放后把它交给手动重新编译。这样不会同时写同一输出目录。
            next_status = (
                "PROCESSING" if (current_is_running and running_lease) else "PENDING"
            )
            crud_yolo_bm1688_export.update_row(
                db,
                current,
                status=next_status,
                worker_action="recompile",
                queue_lease_owner=(
                    None if next_status == "PENDING" else current.queue_lease_owner
                ),
                queue_lease_expires_at=(
                    None if next_status == "PENDING" else current.queue_lease_expires_at
                ),
                error_message=(
                    "手动重新编译已排队，当前自动编译完成后将覆盖其制品"
                    if next_status == "PROCESSING"
                    else None
                ),
            )
            _invalidate_edge_validation_for_recompile(db, export_id)

    if current_is_running and running_lease:
        _schedule_recompile_after_current(export_id)
    else:
        loop = asyncio.get_event_loop()
        if dedicated_bm1688_worker_enabled():
            pass
        else:
            loop.run_in_executor(None, _run_bm1688_recompile_sync, export_id)

    fresh = crud_yolo_bm1688_export.get(db, export_id)
    task = crud_task.get(db=db, task_id=row.task_id)
    return _export_detail_response(
        fresh or row, task.name if task else None, db, current_user=current_user
    )


def resolve_bmodel_download_path(row, filename: Optional[str] = None) -> Path:
    models_root = (_export_dir_for_row(row.id) / "models").resolve()
    allowed_names = set(_list_bmodel_names(int(row.id)))

    def _not_ready() -> LabelUException:
        return LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def _forbidden() -> LabelUException:
        return LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    def _validate_model_path(path: Path, relative_name: str) -> Path:
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(models_root).as_posix()
        except (OSError, ValueError):
            raise _forbidden()
        if relative != relative_name or relative not in allowed_names:
            raise _not_ready()
        if not resolved.is_file():
            raise _not_ready()
        return resolved

    def _validate_manifest(path: Path, relative_name: str) -> None:
        """When present, the persisted manifest is the immutable BModel source of truth."""
        raw_manifest = str(getattr(row, "artifact_manifest_json", "") or "").strip()
        if not raw_manifest:
            return  # historical exports without a manifest remain read-only compatible
        try:
            manifest = json.loads(raw_manifest)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _not_ready()
        artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
        if not isinstance(artifacts, list):
            raise _not_ready()
        expected = None
        for asset in artifacts:
            if not isinstance(asset, dict) or asset.get("role") != "model_bmodel":
                continue
            asset_relative = str(asset.get("relative_path") or "").replace("\\", "/")
            if asset_relative.startswith("models/"):
                asset_relative = asset_relative[7:]
            if asset_relative == relative_name:
                expected = asset
                break
        if expected is None:
            raise _not_ready()
        expected_sha256 = str(expected.get("sha256") or "").strip().lower()
        expected_bytes = expected.get("bytes")
        if len(expected_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in expected_sha256
        ):
            raise _not_ready()
        try:
            actual_bytes = path.stat().st_size
            expected_bytes = int(expected_bytes)
        except (OSError, TypeError, ValueError):
            raise _not_ready()
        if actual_bytes != expected_bytes:
            raise _not_ready()
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            raise _not_ready()
        if digest.hexdigest() != expected_sha256:
            raise _not_ready()

    if filename:
        fn = filename.replace("\\", "/").strip()
        pure = PurePosixPath(fn)
        if not fn or pure.is_absolute() or ".." in pure.parts:
            raise _forbidden()
        relative_name = (
            pure.as_posix()
            if "/" in fn
            else f"{cfg_io.chip_to_platform_dir(_compile_platforms()[0])}/{pure.name}"
        )
        resolved = _validate_model_path(models_root / relative_name, relative_name)
        _validate_manifest(resolved, relative_name)
        return resolved
    if row.bmodel_rel_path:
        stored_path = resolve_bm1688_media_file(row.bmodel_rel_path)
        try:
            relative_name = stored_path.relative_to(models_root).as_posix()
        except ValueError:
            raise _forbidden()
        resolved = _validate_model_path(stored_path, relative_name)
        _validate_manifest(resolved, relative_name)
        return resolved
    raise _not_ready()


def _purge_export_disk(
    export_id: int,
    *,
    onnx_rel_path: Optional[str] = None,
    bmodel_rel_path: Optional[str] = None,
) -> None:
    from labelu.internal.application.service.board_eval_pack import (
        clear_board_eval_pack_for_export,
    )

    try:
        clear_board_eval_pack_for_export(int(export_id))
    except OSError as exc:
        logger.warning("clear board eval pack export_id={}: {}", export_id, exc)
    yolo_train_service._rmtree_under_media(_export_dir_for_row(export_id))
    yolo_train_service._unlink_under_media(onnx_rel_path)
    yolo_train_service._unlink_under_media(bmodel_rel_path)


def delete_export(
    db: Session,
    *,
    export_id: int,
    current_user: User,
) -> dict[str, Any]:
    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    if row.status in ("PENDING", "PROCESSING"):
        raise LabelUException(
            code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="导出或编译进行中，请等待结束后再删除",
        )
    if is_export_active(export_id):
        raise LabelUException(
            code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="导出或编译进行中，请等待结束后再删除",
        )
    if crud_yolo_board_validation.has_active_for_export(db, export_id=export_id):
        raise LabelUException(
            code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="存在进行中的板端验证任务，请等待结束后再删除",
        )
    if crud_yolo_edge_validation_job.has_active_for_export(db, export_id=export_id):
        raise LabelUException(
            code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="存在进行中的边端验证任务，请等待结束后再删除",
        )

    snapshot = {
        "id": int(row.id),
        "task_id": int(row.task_id),
        "onnx_rel_path": row.onnx_rel_path,
        "bmodel_rel_path": row.bmodel_rel_path,
    }
    with begin_transaction(db):
        crud_yolo_board_validation.delete_all_for_export(db, export_id=export_id)
        crud_yolo_edge_validation_job.delete_all_for_export(db, export_id=export_id)
        deleted = crud_yolo_bm1688_export.delete(db, export_id)
    if not deleted:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _purge_export_disk(
        snapshot["id"],
        onnx_rel_path=snapshot.get("onnx_rel_path"),
        bmodel_rel_path=snapshot.get("bmodel_rel_path"),
    )
    logger.info(
        "deleted yolo bm1688 export id={} task_id={} user_id={}",
        snapshot["id"],
        snapshot["task_id"],
        current_user.id,
    )
    return {"deleted": True, "export_id": snapshot["id"]}
