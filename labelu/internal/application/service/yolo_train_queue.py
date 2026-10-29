"""YOLO 训练 GPU 资源排队：资源不足时保持 PENDING，有空位后按 FIFO 开训。"""

from __future__ import annotations

import os
import multiprocessing as mp
import re
import subprocess
import threading
import uuid
import json
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional, Tuple

from loguru import logger
from sqlalchemy import and_, case, func, or_

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.execution_fencing import execution_lease_is_active
from labelu.internal.common.yolo_train_gpu_allocator import (
    allocation_unavailable_reason,
    build_gpu_occupancy,
    effective_min_free_mb_for_job,
    release_job_gpu_allocation,
    train_gpu_pool_indices,
    try_allocate_gpu_for_job,
    yolo_train_gpu_auto_alloc_enabled,
)
from labelu.internal.common.yolo_train_resource_profile import (
    get_yolo_train_resource_profile,
    log_yolo_train_resource_profile_once,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_QUEUE_LOCK = threading.Lock()
_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()
_IS_WINDOWS = os.name == "nt"


def _job_id_in_workdir_pattern() -> re.Pattern[str]:
    return re.compile(
        rf"/{re.escape(settings.YOLO_TRAIN_WORK_SUBDIR)}/job-(\d+)(?:/|$)"
    )


@dataclass(frozen=True)
class _QueuedTrainStart:
    job_id: int
    task_id: int
    cmd: CreateYoloTrainJobCommand
    resume: bool
    queue_source: str = "MANUAL"
    queue_priority: int = 0


_TRAIN_START_QUEUE: Deque[_QueuedTrainStart] = deque()


def pending_job_ordering_key(
    job_id: int, queue_source: str | None, queue_priority: int | None
) -> tuple[int, int, int]:
    source_rank = 1 if str(queue_source or "MANUAL").upper() == "AUTO" else 0
    return source_rank, int(queue_priority or 0), int(job_id)


def _queue_metadata(job_id: int) -> tuple[str, int]:
    try:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
            if job is not None:
                automatic_lineage = (
                    str(getattr(job, "queue_source", None) or "").upper() == "AUTO"
                    or getattr(job, "auto_training_plan_id", None) is not None
                    or getattr(job, "auto_training_round_id", None) is not None
                )
                return (
                    "AUTO"
                    if automatic_lineage
                    else str(getattr(job, "queue_source", None) or "MANUAL"),
                    int(getattr(job, "queue_priority", None) or 0),
                )
    except Exception:
        logger.exception("failed to load train queue metadata for job {}", job_id)
    return "MANUAL", 0


def _is_automatic_train_job(job: YoloTrainJob) -> bool:
    """Return whether a persisted job belongs to the automatic pipeline."""
    return bool(
        str(getattr(job, "queue_source", None) or "").upper() == "AUTO"
        or getattr(job, "auto_training_plan_id", None) is not None
        or getattr(job, "auto_training_round_id", None) is not None
    )


def command_from_job(job: YoloTrainJob) -> CreateYoloTrainJobCommand:
    from labelu.internal.application.service.yolo_train import (
        _job_hyperparams_dict,
        parse_source_task_ids,
    )

    # A persisted training request is the Worker source of truth.  Empty
    # legacy rows remain compatible, but a non-empty malformed JSON value must
    # fail closed instead of silently training with default hyperparameters.
    raw_hyperparams = getattr(job, "hyperparams_json", None)
    if raw_hyperparams not in (None, ""):
        try:
            decoded_hyperparams = json.loads(raw_hyperparams)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("训练作业超参持久化内容不可解析，拒绝启动") from exc
        if not isinstance(decoded_hyperparams, dict):
            raise ValueError("训练作业超参持久化内容必须是对象，拒绝启动")

    source_ids = parse_source_task_ids(job)
    mode = str(getattr(job, "execution_mode", None) or "local").strip() or "local"
    return CreateYoloTrainJobCommand(
        model_weights=job.model_weights or "yolo11n.pt",
        weights_source=None,
        epochs=int(job.epochs or 50),
        imgsz=int(job.imgsz or 640),
        category=getattr(job, "category", None) or "",
        hyperparams=_job_hyperparams_dict(job),
        merge_task_ids=source_ids[1:] if len(source_ids) > 1 else None,
        execution_mode="remote" if mode == "remote" else "local",
        train_node_id=getattr(job, "train_node_id", None) or None,
    )


def _read_proc_cmdline(pid: int) -> str:
    try:
        raw = open(f"/proc/{int(pid)}/cmdline", "rb").read()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return ""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        return _windows_pid_alive(pid)
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except (OSError, SystemError):
        return False


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, wintypes.LPDWORD)
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        int(pid),
    )
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == still_active
    finally:
        kernel32.CloseHandle(handle)


def _linux_ppid(pid: int) -> int:
    try:
        stat = open(f"/proc/{int(pid)}/stat", encoding="utf-8").read()
        # pid (comm) state ppid ...
        after_comm = stat.rsplit(")", 1)[-1].split()
        return int(after_comm[1])
    except (OSError, ValueError, IndexError):
        return 0


def _job_ids_in_cmdline(cmd: str) -> set[int]:
    out: set[int] = set()
    for match in _job_id_in_workdir_pattern().finditer(cmd or ""):
        out.add(int(match.group(1)))
    return out


def resolve_job_ids_from_process_pid(pid: int, *, max_depth: int = 24) -> set[int]:
    """
    从 GPU/DDP 子进程向上遍历进程树，匹配 yolo_train/job-{id} 工作目录。
    Ultralytics DDP 占卡进程 cmdline 仅为 _temp_*.py，需靠祖先进程关联 job。
    """
    found: set[int] = set()
    current = int(pid)
    depth = 0
    while current > 1 and depth < max_depth:
        depth += 1
        cmd = _read_proc_cmdline(current)
        found.update(_job_ids_in_cmdline(cmd))
        if found:
            return found
        try:
            cwd = os.readlink(f"/proc/{current}/cwd")
            found.update(_job_ids_in_cmdline(cwd))
            if found:
                return found
        except OSError:
            pass
        current = _linux_ppid(current)
    return found


def active_yolo_train_job_ids_on_gpu() -> set[int]:
    """从 nvidia-smi 计算进程解析 job id（含 DDP 子进程：向上遍历进程树）。"""
    from labelu.internal.application.service.yolo_train import _nvidia_compute_pids

    out: set[int] = set()
    for pid in _nvidia_compute_pids():
        cmd = _read_proc_cmdline(pid)
        ids = _job_ids_in_cmdline(cmd)
        if not ids:
            ids = resolve_job_ids_from_process_pid(int(pid))
        out.update(ids)
    return out


def _gpu_uuid_to_index() -> dict[str, int]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    mapping: dict[str, int] = {}
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            mapping[parts[1]] = int(parts[0])
        except ValueError:
            continue
    return mapping


def _gpu_occupancy_extra_from_nvidia_smi() -> dict[int, Optional[int]]:
    """重启后从 nvidia-smi 反查 LabelU 训练 job 占用的 GPU。"""
    uuid_map = _gpu_uuid_to_index()
    if not uuid_map:
        return {}
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    pattern = _job_id_in_workdir_pattern()
    extra: dict[int, Optional[int]] = {}
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        gpu_idx = uuid_map.get(parts[0])
        if gpu_idx is None:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        cmd = _read_proc_cmdline(pid)
        if not cmd:
            continue
        for match in pattern.finditer(cmd):
            extra[int(gpu_idx)] = int(match.group(1))
            break
    return extra


def _build_occupied_gpus(
    *, exclude_job_id: Optional[int] = None
) -> dict[int, Optional[int]]:
    return build_gpu_occupancy(
        exclude_job_id=exclude_job_id,
        extra_from_smi=_gpu_occupancy_extra_from_nvidia_smi(),
    )


def count_active_yolo_train_slots(*, exclude_job_id: Optional[int] = None) -> int:
    from labelu.internal.application.service.yolo_train import _ACTIVE_TRAIN_JOBS

    slots: set[int] = set()
    for jid in _ACTIVE_TRAIN_JOBS:
        if exclude_job_id is None or int(jid) != int(exclude_job_id):
            slots.add(int(jid))
    for jid in active_yolo_train_job_ids_on_gpu():
        if exclude_job_id is None or int(jid) != int(exclude_job_id):
            slots.add(int(jid))
    return len(slots)


def _gpu_free_memory_mb() -> dict[int, int]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    out: dict[int, int] = {}
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            out[int(parts[0])] = int(float(parts[1]))
        except ValueError:
            continue
    return out


def _yolo_train_resources_available_fixed(
    *, exclude_job_id: Optional[int] = None
) -> Tuple[bool, str]:
    """固定 YOLO_TRAIN_DEVICE（未开自动分配）时的并发与显存检查。"""
    queue_busy = bool(_TRAIN_START_QUEUE)
    profile = get_yolo_train_resource_profile(queue_has_waiters=queue_busy)

    max_concurrent = max(1, int(profile.max_concurrent_jobs))
    active = count_active_yolo_train_slots(exclude_job_id=exclude_job_id)
    if active >= max_concurrent:
        return (
            False,
            f"已有 {active} 个训练作业占用 GPU（上限 {max_concurrent}）",
        )

    if profile.cpu_only:
        return True, ""

    min_free = int(profile.min_free_mb_per_gpu)
    if min_free > 0 and profile.train_gpu_indices:
        free_map = _gpu_free_memory_mb()
        for did in profile.train_gpu_indices:
            free_mb = free_map.get(did)
            if free_mb is None:
                continue
            if free_mb < min_free:
                return (
                    False,
                    f"GPU {did} 可用显存 {free_mb}MB < 自动阈值 {min_free}MB",
                )
    return True, ""


def _resolve_train_start_slot(
    job_id: int,
    cmd: CreateYoloTrainJobCommand,
) -> Tuple[bool, str, Optional[str]]:
    """检查能否开训；自动分配模式下返回预分配的 device 字符串。"""
    if not settings.YOLO_TRAIN_ENABLED:
        return False, "YOLO 训练未启用", None

    if not yolo_train_gpu_auto_alloc_enabled():
        ok, reason = _yolo_train_resources_available_fixed(exclude_job_id=job_id)
        # Fixed/single-GPU mode still needs an explicit device passed to the
        # child process.  ``None`` is treated as CPU by the child binder, which
        # would otherwise clear CUDA_VISIBLE_DEVICES even when GPU 0 is ready.
        if not ok:
            return False, reason, None
        from labelu.internal.application.service.yolo_train import (
            _resolve_yolo_train_device,
        )

        return True, reason, _resolve_yolo_train_device()

    profile = get_yolo_train_resource_profile(
        queue_has_waiters=bool(_TRAIN_START_QUEUE)
    )
    if profile.cpu_only:
        return True, "", None

    free_map = _gpu_free_memory_mb()
    occupied = _build_occupied_gpus(exclude_job_id=job_id)
    min_free = effective_min_free_mb_for_job(
        cmd, base_min_mb=int(profile.min_free_mb_per_gpu)
    )
    plan = try_allocate_gpu_for_job(
        int(job_id),
        cmd,
        occupied_by_gpu=occupied,
        free_mb=free_map,
        min_free_mb=min_free,
        queued_job_count=len(_TRAIN_START_QUEUE),
    )
    if plan:
        from labelu.internal.application.service.yolo_train import (
            _append_train_process_log,
        )

        _append_train_process_log(
            int(job_id),
            (
                f"gpu_alloc device={plan.device} tier={plan.tier} "
                f"score={plan.load_score} ({plan.reason})"
            ),
        )
        return True, "", plan.device

    reason = allocation_unavailable_reason(
        cmd,
        occupied_by_gpu=occupied,
        free_mb=free_map,
        min_free_mb=min_free,
    )
    return False, reason, None


def yolo_train_resources_available(
    *, exclude_job_id: Optional[int] = None
) -> Tuple[bool, str]:
    if not settings.YOLO_TRAIN_ENABLED:
        return False, "YOLO 训练未启用"

    if yolo_train_gpu_auto_alloc_enabled():
        profile = get_yolo_train_resource_profile(
            queue_has_waiters=bool(_TRAIN_START_QUEUE)
        )
        if profile.cpu_only:
            return True, ""
        occupied = _build_occupied_gpus(exclude_job_id=exclude_job_id)
        free_map = _gpu_free_memory_mb()
        pool = profile.train_gpu_indices
        if not pool:
            return False, "无可用训练 GPU"
        # 用最低门槛估算「是否有任意空槽」（具体作业开训时仍按任务档位算门槛）
        slot_min = int(profile.min_free_mb_per_gpu)
        small_min = int(
            getattr(settings, "YOLO_TRAIN_GPU_MIN_FREE_MB_SMALL", 4096) or 4096
        )
        threshold = min(slot_min, small_min)
        free_slots = [
            i
            for i in pool
            if occupied.get(i) is None and free_map.get(i, threshold + 1) >= threshold
        ]
        if free_slots:
            return True, ""
        return False, f"训练 GPU {list(pool)} 无足够空闲槽位"

    return _yolo_train_resources_available_fixed(exclude_job_id=exclude_job_id)


def _queued_job_ids() -> set[int]:
    return {item.job_id for item in _TRAIN_START_QUEUE}


def _normalize_train_phase_tuple(value):
    if not isinstance(value, tuple):
        return None, None, None, None
    if len(value) >= 4:
        return value[0], value[1], value[2], value[3]
    if len(value) == 3:
        return value[0], value[1], value[2], None
    if len(value) == 2:
        return value[0], value[1], None, None
    if len(value) == 1:
        return value[0], None, None, None
    return None, None, None, None


def _has_active_execution_lease(job: Any) -> bool:
    """Return whether a legacy Web/queue path must defer to a Worker."""
    return execution_lease_is_active(job)


def _mark_job_queued_for_resources(job_id: int, reason: str) -> None:
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _write_train_phase,
    )

    detail = f"等待训练资源：{reason}" if reason else "等待训练资源"
    _write_train_phase(int(job_id), "queued", detail=detail)
    _append_train_process_log(
        int(job_id),
        f"phase=queued detail=waiting for GPU resources ({reason or 'busy'})",
    )
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
        if not job:
            return
        if _has_active_execution_lease(job):
            logger.info(
                "skip legacy queue reset for job {} held by execution worker",
                job_id,
            )
            return
        if str(job.status or "") == YoloTrainStatus.PROCESSING.value:
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
                if not job or _has_active_execution_lease(job):
                    return
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    YoloTrainStatus.PENDING.value,
                    error_message=None,
                )


def _bind_train_child_cuda_device(train_device: Optional[str]) -> str:
    """Bind physical GPUs before the child imports torch/Ultralytics."""
    raw = str(train_device or "").strip().lower()
    if not raw or raw == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        return "cpu"
    physical_indices = [
        int(part.replace("cuda:", "").strip())
        for part in raw.split(",")
        if part.strip()
    ]
    if not physical_indices:
        physical_indices = [0]
    os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(
        str(index) for index in physical_indices
    )
    # Assigned physical cards are exposed as contiguous logical devices in the child.
    return ",".join(str(index) for index in range(len(physical_indices)))


def _run_plate_recognition_train_child(
    job_id: int,
    task_id: int,
    epochs: int,
    imgsz: int,
    hyperparams: Optional[dict],
    train_device: Optional[str],
    resume: bool,
    execution_fencing_token: Optional[int] = None,
    execution_owner: Optional[str] = None,
) -> None:
    logical_device = _bind_train_child_cuda_device(train_device)
    with session_scope() as db:
        current_job = crud_yolo_train_job.get(db, int(job_id))
        job_type = str(
            getattr(current_job, "job_type", None) or "plate_recognition"
        ).strip()
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _disable_ultralytics_online_amp_check,
        _write_train_phase,
    )

    _append_train_process_log(
        int(job_id),
        (
            f"plate_child_start pid={os.getpid()} physical_device={train_device or 'cpu'} "
            f"logical_device={logical_device} resume={bool(resume)}"
        ),
    )
    _write_train_phase(
        int(job_id),
        "running",
        detail=(
            f"{job_type} physical_device={train_device or 'cpu'} "
            f"logical_device={logical_device}"
        ),
        execution_owner=execution_owner,
        execution_fencing_token=execution_fencing_token,
    )
    _disable_ultralytics_online_amp_check(job_id=int(job_id))
    from labelu.internal.application.service.plate_recognition_train import (
        run_plate_detection_train_job,
        run_plate_recognition_train_job,
    )

    runner = (
        run_plate_detection_train_job
        if job_type == "plate_detection"
        else run_plate_recognition_train_job
    )
    runner(
        job_id=int(job_id),
        task_id=int(task_id),
        epochs=int(epochs),
        imgsz=int(imgsz),
        hyperparams=hyperparams,
        train_device=logical_device,
        resume=bool(resume),
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )
    _write_train_phase(
        int(job_id),
        "completed",
        detail=f"{job_type} training completed",
        execution_owner=execution_owner,
        execution_fencing_token=execution_fencing_token,
    )


def _run_yolo_train_child(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    resume: bool,
    train_device: Optional[str],
    execution_fencing_token: Optional[int] = None,
    execution_owner: Optional[str] = None,
) -> None:
    logical_device = _bind_train_child_cuda_device(train_device)
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _run_yolo_train_worker,
    )

    _append_train_process_log(
        int(job_id),
        (
            f"yolo_child_start pid={os.getpid()} physical_device={train_device or 'cpu'} "
            f"logical_device={logical_device} resume={bool(resume)}"
        ),
    )
    _run_yolo_train_worker(
        int(job_id),
        int(task_id),
        cmd,
        resume=bool(resume),
        train_device=logical_device,
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )


def _join_train_process_with_lease(proc, *, lease_check=None) -> None:
    """等待训练子进程，并在执行租约失效时立即终止它。"""
    if lease_check is None:
        proc.join()
        return
    try:
        has_exited = not proc.is_alive()
    except AttributeError:
        proc.join()
        lease_check()
        return
    while not has_exited:
        proc.join(timeout=0.5)
        try:
            lease_check()
        except Exception:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
            raise
        has_exited = not proc.is_alive()


def _update_runtime_pid_or_terminate(proc, runtime_update=None) -> None:
    if runtime_update is None:
        return
    try:
        runtime_update(int(proc.pid))
    except Exception:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
        raise


def _handle_spawn_child_exit_with_lease(
    job_id: int,
    exit_code: int | None,
    *,
    lease_check=None,
    execution_fencing_token=None,
    execution_owner=None,
) -> None:
    """拒绝失去执行租约的 worker 写入子进程收尾状态。

    训练子进程本身仍负责兼容 Web 路径的状态写入；独立 Worker 的父进程
    在调用收尾状态处理器前再检查一次 owner/token，覆盖“子进程已退出但
    lease 刚被接管”的窄窗口。
    """
    if lease_check is not None:
        lease_check()
    from labelu.internal.application.service.yolo_train import _handle_spawn_child_exit

    if execution_fencing_token is None and execution_owner is None:
        # Keep isolated tests and historical callers compatible with the
        # two-argument lifecycle hook.
        _handle_spawn_child_exit(int(job_id), exit_code)
    else:
        _handle_spawn_child_exit(
            int(job_id),
            exit_code,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )


def _run_plate_recognition_train_subprocess(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool,
    train_device: Optional[str],
    lease_check=None,
    runtime_update=None,
    execution_fencing_token=None,
    execution_owner=None,
) -> None:
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _write_train_phase,
        is_stop_requested,
    )

    ctx = mp.get_context("spawn")
    child_args = [
        int(job_id),
        int(task_id),
        int(cmd.epochs),
        int(cmd.imgsz),
        cmd.hyperparams,
        train_device,
        bool(resume),
    ]
    if execution_fencing_token is not None or execution_owner is not None:
        child_args.extend([execution_fencing_token, execution_owner])
    proc = ctx.Process(
        target=_run_plate_recognition_train_child,
        args=tuple(child_args),
        name=f"plate-train-{int(job_id)}",
    )
    proc.start()
    _update_runtime_pid_or_terminate(proc, runtime_update)
    phase_kwargs = {
        "detail": f"plate_recognition subprocess device={train_device or 'cpu'}",
        "runtime_pid": int(proc.pid),
    }
    if execution_owner is not None or execution_fencing_token is not None:
        phase_kwargs.update(
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
    _write_train_phase(int(job_id), "running", **phase_kwargs)
    _join_train_process_with_lease(proc, lease_check=lease_check)
    exit_code = proc.exitcode
    if exit_code not in (0, None) and is_stop_requested(int(job_id)):
        _append_train_process_log(
            int(job_id),
            f"plate_child_stopped pid={proc.pid} exit_code={exit_code}",
        )
        return
    if exit_code not in (0, None):
        _handle_spawn_child_exit_with_lease(
            int(job_id),
            exit_code,
            lease_check=lease_check,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )


def _run_yolo_train_subprocess(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool,
    train_device: Optional[str],
    lease_check=None,
    runtime_update=None,
    execution_fencing_token=None,
    execution_owner=None,
) -> None:
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _write_train_phase,
        is_stop_requested,
    )

    ctx = mp.get_context("spawn")
    child_args = [int(job_id), int(task_id), cmd, bool(resume), train_device]
    if execution_fencing_token is not None or execution_owner is not None:
        child_args.extend([execution_fencing_token, execution_owner])
    proc = ctx.Process(
        target=_run_yolo_train_child,
        args=tuple(child_args),
        name=f"yolo-train-{int(job_id)}",
    )
    proc.start()
    _update_runtime_pid_or_terminate(proc, runtime_update)
    phase_kwargs = {
        "detail": f"yolo subprocess device={train_device or 'cpu'}",
        "runtime_pid": int(proc.pid),
    }
    if execution_owner is not None or execution_fencing_token is not None:
        phase_kwargs.update(
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
    _write_train_phase(int(job_id), "running", **phase_kwargs)
    _join_train_process_with_lease(proc, lease_check=lease_check)
    exit_code = proc.exitcode
    if exit_code not in (0, None) and is_stop_requested(int(job_id)):
        _append_train_process_log(
            int(job_id),
            f"yolo_child_stopped pid={proc.pid} exit_code={exit_code}",
        )
        return
    _handle_spawn_child_exit_with_lease(
        int(job_id),
        exit_code,
        lease_check=lease_check,
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )


def _start_yolo_train_job_immediate(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool = False,
    train_device: Optional[str] = None,
) -> None:
    import asyncio
    import threading

    from labelu.internal.application.service.yolo_train import (
        _ACTIVE_TRAIN_JOBS,
    )

    jid = int(job_id)
    _ACTIVE_TRAIN_JOBS.add(jid)

    def _worker_wrapper() -> None:
        try:
            with session_scope() as db:
                current_job = crud_yolo_train_job.get(db, jid)
                job_type = str(
                    getattr(current_job, "job_type", None) or "yolo_detection"
                ).strip()
            if job_type in {"plate_detection", "plate_recognition"}:
                _run_plate_recognition_train_subprocess(
                    job_id,
                    task_id,
                    cmd,
                    resume=resume,
                    train_device=train_device,
                )
            else:
                _run_yolo_train_subprocess(
                    job_id,
                    task_id,
                    cmd,
                    resume=resume,
                    train_device=train_device,
                )
        finally:
            release_job_gpu_allocation(jid)
            _ACTIVE_TRAIN_JOBS.discard(jid)
            dispatch_all_available_train_jobs()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(
            target=_worker_wrapper,
            daemon=True,
            name=f"yolo-train-{job_id}",
        ).start()
        return
    loop.run_in_executor(None, _worker_wrapper)


def request_train_start(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool = False,
) -> bool:
    """尝试开训；若资源不足或前方有排队则入队并返回 False。"""
    jid = int(job_id)
    mode = str(getattr(cmd, "execution_mode", None) or "local").strip()
    # 独立 Worker 开关开启且执行契约完整时，Web 进程只保留 PENDING，
    # 由独立进程用数据库租约领取，避免两套消费者同时启动同一作业。
    from labelu.internal.application.service.yolo_train_worker import (
        dedicated_worker_ready,
    )

    if mode != "remote" and bool(
        getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", False)
    ):
        ready, reason = dedicated_worker_ready()
        if ready:
            _mark_job_queued_for_resources(jid, "等待独立训练 Worker 领取")
            logger.info("YOLO train job {} held for dedicated worker", jid)
            return False
        # 开关打开后禁止回落到 Web 线程；否则部署时 Worker 启动失败会
        # 重新启用第二套消费者，造成重复训练、GPU 争抢和旧进程迟到写入。
        _mark_job_queued_for_resources(
            jid, f"独立训练 Worker 未就绪，等待运维修复：{reason}"
        )
        logger.error(
            "独立训练 Worker 未就绪，训练作业 {} 保持持久队列，不回落 Web 路径：{}",
            jid,
            reason,
        )
        return False
    queue_source, queue_priority = _queue_metadata(jid)
    if (
        queue_source.upper() == "AUTO"
        and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
    ):
        logger.info("automatic YOLO train job {} held by phase-one read-only gate", jid)
        return False
    if mode == "remote":
        from labelu.internal.application.service.remote_yolo_train import (
            request_remote_train_start,
        )

        request_remote_train_start(jid, int(task_id), cmd, resume=resume)
        return True

    from labelu.internal.application.service.yolo_train import _ACTIVE_TRAIN_JOBS

    if jid in _ACTIVE_TRAIN_JOBS:
        logger.info(
            "YOLO train job {} already active in worker, skip duplicate start", jid
        )
        return False
    item = _QueuedTrainStart(
        job_id=jid,
        task_id=int(task_id),
        cmd=cmd,
        resume=resume,
        queue_source=queue_source,
        queue_priority=queue_priority,
    )
    with _QUEUE_LOCK:
        if jid in _queued_job_ids():
            return False
        ok, reason, train_device = _resolve_train_start_slot(jid, cmd)
        if ok:
            _start_yolo_train_job_immediate(
                jid,
                int(task_id),
                cmd,
                resume=resume,
                train_device=train_device,
            )
            logger.info("YOLO train job {} started immediately", jid)
            return True
        ahead = bool(_TRAIN_START_QUEUE)
        pending = list(_TRAIN_START_QUEUE)
        pending.append(item)
        pending.sort(
            key=lambda queued: pending_job_ordering_key(
                queued.job_id, queued.queue_source, queued.queue_priority
            )
        )
        _TRAIN_START_QUEUE.clear()
        _TRAIN_START_QUEUE.extend(pending)
        queue_reason = reason or (
            "前方有排队训练作业" if ahead else "等待训练 GPU 资源"
        )
        _mark_job_queued_for_resources(jid, queue_reason)
        logger.info("YOLO train job {} queued: {}", jid, queue_reason)
        return False


def dispatch_queued_train_jobs() -> int:
    """资源允许时派发排队作业；队首阻塞时仍尝试后续可开训项。"""
    from labelu.internal.application.service.yolo_train_worker import (
        dedicated_worker_ready,
    )

    if bool(getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", False)):
        ready, reason = dedicated_worker_ready()
        if ready:
            return 0
        logger.error(
            "独立训练 Worker 未就绪，保留内存队列且禁止 Web 消费：{}", reason
        )
        return 0
    started = 0
    with _QUEUE_LOCK:
        if not _TRAIN_START_QUEUE:
            return 0
        pending = list(_TRAIN_START_QUEUE)
        _TRAIN_START_QUEUE.clear()
        for item in pending:
            if (
                item.queue_source.upper() == "AUTO"
                and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
            ):
                logger.info(
                    "automatic YOLO train job {} removed from in-memory dispatch queue",
                    item.job_id,
                )
                continue
            mode = str(getattr(item.cmd, "execution_mode", None) or "local").strip()
            if mode == "remote":
                from labelu.internal.application.service.remote_yolo_train import (
                    request_remote_train_start,
                )

                request_remote_train_start(
                    item.job_id,
                    item.task_id,
                    item.cmd,
                    resume=item.resume,
                )
                started += 1
                logger.info(
                    "YOLO train job {} dequeued for remote dispatch",
                    item.job_id,
                )
                continue
            ok, reason, train_device = _resolve_train_start_slot(item.job_id, item.cmd)
            if not ok:
                _TRAIN_START_QUEUE.append(item)
                continue
            _start_yolo_train_job_immediate(
                item.job_id,
                item.task_id,
                item.cmd,
                resume=item.resume,
                train_device=train_device,
            )
            started += 1
            logger.info(
                "YOLO train job {} dequeued and started (reason cleared: {})",
                item.job_id,
                reason or "ok",
            )
    return started


def dispatch_all_available_train_jobs() -> int:
    """一次唤醒尽量填满所有空闲 GPU 槽位（双卡服务器上 2 个小任务可并行）。"""
    total = 0
    for _ in range(max(4, len(train_gpu_pool_indices()) * 2)):
        n = dispatch_queued_train_jobs()
        if n <= 0:
            break
        total += n
    return total


def reconcile_stale_processing_jobs() -> int:
    """无活跃 worker 的 PROCESSING 僵尸作业回退，避免占槽/假「训练中」。"""
    from labelu.internal.application.service.yolo_train import (
        _ACTIVE_TRAIN_JOBS,
        _append_train_process_log,
        _read_train_phase,
        _runtime_train_process_on_gpu,
    )

    gpu_jobs = active_yolo_train_job_ids_on_gpu()
    fixed = 0
    with session_scope() as db:
        rows = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.status == YoloTrainStatus.PROCESSING.value)
            .all()
        )
        for job in rows:
            jid = int(job.id)
            if jid in _queued_job_ids():
                continue
            if _has_active_execution_lease(job):
                logger.info(
                    "skip stale reconcile for job {} held by execution worker",
                    jid,
                )
                continue
            if str(getattr(job, "execution_mode", None) or "local").strip() == "remote":
                from labelu.internal.application.service.remote_yolo_train import (
                    remote_job_runtime_status,
                )

                if remote_job_runtime_status(job) == "RUNNING":
                    continue
            phase, _label, runtime_pid, _detail = _normalize_train_phase_tuple(
                _read_train_phase(jid)
            )
            on_gpu = jid in gpu_jobs or (
                runtime_pid is not None
                and _runtime_train_process_on_gpu(int(runtime_pid))
            )
            # 父进程 worker 线程仍持有该 job（双卡 spawn 在 proc.join 中）时，禁止 reconcile 误杀。
            # DDP 占卡进程 cmdline 无 job 路径，on_gpu 在首几个 epoch 前可能长期为 False。
            if jid in _ACTIVE_TRAIN_JOBS:
                continue
            if runtime_pid is not None and _pid_alive(int(runtime_pid)):
                continue
            elif jid in gpu_jobs:
                continue
            if phase in (
                "running",
                "restarting",
                "loading_model",
                "training",
                "exporting",
            ):
                pass  # 无 GPU 进程时落到下方 PENDING
            elif phase not in ("queued", "failed", None, ""):
                continue
            if phase == "failed":
                with begin_transaction(db):
                    job = crud_yolo_train_job.get(db=db, job_id=jid)
                    if not job or _has_active_execution_lease(job):
                        continue
                    crud_yolo_train_job.update_status_if_execution_lease_free(
                        db,
                        jid,
                        YoloTrainStatus.FAILED.value,
                        error_message=_detail[:500] if _detail else None,
                    )
                _append_train_process_log(
                    jid,
                    "phase=reconcile stale PROCESSING+failed -> FAILED",
                )
                fixed += 1
                continue
            if on_gpu:
                continue
            if phase in (
                "running",
                "restarting",
                "loading_model",
                "training",
                "exporting",
            ):
                target_status = YoloTrainStatus.PENDING.value
                log_msg = f"phase=reconcile stale PROCESSING+{phase} (no GPU worker) -> PENDING"
            elif phase not in ("queued", None, ""):
                continue
            else:
                target_status = YoloTrainStatus.PENDING.value
                log_msg = (
                    f"phase=reconcile stale PROCESSING+{phase or 'unknown'} -> PENDING"
                )
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db=db, job_id=jid)
                if not job or _has_active_execution_lease(job):
                    continue
                crud_yolo_train_job.update_status_if_execution_lease_free(
                    db,
                    jid,
                    target_status,
                    error_message=None,
                )
            _append_train_process_log(jid, log_msg)
            fixed += 1
    if fixed:
        logger.info("reconcile_stale_processing_jobs: reset {} job(s)", fixed)
    return fixed


def drain_db_pending_train_jobs() -> int:
    """服务重启后把库中 PENDING 且未在内存队列的作业重新提交排队。"""
    from labelu.internal.application.service.yolo_train_worker import (
        dedicated_worker_ready,
    )

    if bool(getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", False)):
        ready, reason = dedicated_worker_ready()
        logger.info(
            "独立训练 Worker 开关已开启，Web 进程不恢复数据库训练作业（ready={} reason={}）",
            ready,
            reason,
        )
        return 0
    from labelu.internal.application.service.yolo_train import (
        _ACTIVE_TRAIN_JOBS,
        clear_stop_train_job,
        is_user_pause_requested,
    )

    if not settings.YOLO_TRAIN_ENABLED:
        return 0

    now = datetime.now()

    def _release_claim(job_id: int, owner: str) -> None:
        """Release only this drain's claim after a failed/invalid handoff."""
        with session_scope() as db:
            with begin_transaction(db):
                db.query(YoloTrainJob).filter(
                    YoloTrainJob.id == job_id,
                    YoloTrainJob.queue_lease_owner == owner,
                ).update(
                    {
                        YoloTrainJob.queue_lease_owner: None,
                        YoloTrainJob.queue_lease_expires_at: datetime.now(),
                    },
                    synchronize_session=False,
                )

    automatic_predicate = or_(
        func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL")) == "AUTO",
        YoloTrainJob.auto_training_plan_id.isnot(None),
        YoloTrainJob.auto_training_round_id.isnot(None),
    )
    manual_predicate = and_(
        func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL")) != "AUTO",
        YoloTrainJob.auto_training_plan_id.is_(None),
        YoloTrainJob.auto_training_round_id.is_(None),
    )
    job_ids: list[int] = []
    with session_scope() as db:
        query = db.query(YoloTrainJob.id).filter(
            YoloTrainJob.status == YoloTrainStatus.PENDING.value
        )
        if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
            query = query.filter(manual_predicate)
        else:
            # Automatic jobs are only eligible after an explicit lease has
            # expired.  A NULL lease is intentionally not recoverable here:
            # the automatic scheduler must first create and own the work.
            query = query.filter(
                or_(
                    manual_predicate,
                    and_(
                        automatic_predicate,
                        YoloTrainJob.queue_lease_expires_at.isnot(None),
                        YoloTrainJob.queue_lease_expires_at <= now,
                    ),
                )
            )
        rows = query.order_by(
            case(
                (
                    func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL"))
                    == "AUTO",
                    1,
                ),
                else_=0,
            ).asc(),
            func.coalesce(YoloTrainJob.queue_priority, 0).asc(),
            YoloTrainJob.id.asc(),
        ).all()
        job_ids = [int(r[0]) for r in rows]

    submitted = 0
    for jid in job_ids:
        if jid in _ACTIVE_TRAIN_JOBS or jid in _queued_job_ids():
            continue
        if is_user_pause_requested(jid):
            continue

        # Manual jobs retain the historical restart behavior.  Automatic jobs
        # must first win a database CAS on the expired scheduler lease so two
        # watchdog processes cannot both submit the same work.
        automatic = False
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=jid)
            if not job or job.status != YoloTrainStatus.PENDING.value:
                continue
            automatic = _is_automatic_train_job(job)

        lease_owner: str | None = None
        if automatic:
            lease_owner = f"train-drain:{os.getpid()}:{uuid.uuid4().hex}"
            lease_expires_at = now + timedelta(seconds=300)
            with session_scope() as db:
                with begin_transaction(db):
                    claimed = (
                        db.query(YoloTrainJob)
                        .filter(
                            YoloTrainJob.id == jid,
                            YoloTrainJob.status == YoloTrainStatus.PENDING.value,
                            automatic_predicate,
                            YoloTrainJob.queue_lease_expires_at.isnot(None),
                            YoloTrainJob.queue_lease_expires_at <= now,
                        )
                        .update(
                            {
                                YoloTrainJob.queue_lease_owner: lease_owner,
                                YoloTrainJob.queue_lease_expires_at: lease_expires_at,
                            },
                            synchronize_session=False,
                        )
                    )
                if claimed != 1:
                    # Another dispatcher won the lease, or the scheduler
                    # renewed it while this process was preparing the job.
                    continue

        try:
            # SIGTERM writes the transient stop flag so the active trainer can
            # exit. A PENDING job without an explicit user-pause marker must be
            # allowed to resume from last.pt after the service starts again.
            clear_stop_train_job(jid)
            with session_scope() as db:
                job = crud_yolo_train_job.get(db=db, job_id=jid)
                if not job or job.status != YoloTrainStatus.PENDING.value:
                    cmd = None
                    task_id = None
                else:
                    cmd = command_from_job(job)
                    task_id = int(job.task_id)
            if cmd is None or task_id is None:
                if lease_owner is not None:
                    _release_claim(jid, lease_owner)
                continue
            from labelu.internal.application.service.yolo_train import _job_work_root

            resume = (_job_work_root(jid) / "train" / "weights" / "last.pt").is_file()
            from labelu.internal.application.service.yolo_train import (
                _submit_yolo_train_job,
            )

            _submit_yolo_train_job(jid, task_id, cmd, resume=resume)
        except Exception:
            if lease_owner is not None:
                _release_claim(jid, lease_owner)
            raise
        submitted += 1
    if submitted:
        logger.info("train queue drain: submitted {} pending job(s)", submitted)
    return submitted


def schedule_train_queue_watchdog() -> None:
    """启动时立即尝试派发；之后周期性扫描排队与库中 PENDING。"""
    global _WATCHDOG_STARTED
    if not settings.YOLO_TRAIN_ENABLED:
        return
    from labelu.internal.application.service.yolo_train_worker import (
        dedicated_worker_ready,
    )

    if bool(getattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", False)):
        ready, reason = dedicated_worker_ready()
        logger.info(
            "独立训练 Worker 开关已开启，Web 训练看门狗关闭（ready={} reason={}）",
            ready,
            reason,
        )
        return

    profile = log_yolo_train_resource_profile_once()
    interval = max(10, int(profile.queue_scan_interval_seconds))

    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _tick() -> None:
        try:
            reconcile_stale_processing_jobs()
            dispatch_all_available_train_jobs()
            drain_db_pending_train_jobs()
        except Exception:
            logger.exception("train queue watchdog tick failed")
        next_profile = get_yolo_train_resource_profile(
            queue_has_waiters=bool(_TRAIN_START_QUEUE)
        )
        next_interval = max(10, int(next_profile.queue_scan_interval_seconds))
        timer = threading.Timer(float(next_interval), _tick)
        timer.daemon = True
        timer.start()

    def _bootstrap() -> None:
        reconcile_stale_processing_jobs()
        dispatch_all_available_train_jobs()
        drain_db_pending_train_jobs()

    threading.Thread(target=_bootstrap, daemon=True).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info(
        "train queue watchdog started (auto interval={}s, {})",
        interval,
        profile.summary(),
    )
