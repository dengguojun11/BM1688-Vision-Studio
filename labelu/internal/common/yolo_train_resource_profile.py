"""根据当前机器 GPU 自动推导 YOLO 训练排队参数（无需 .env 手工配置）。"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from loguru import logger

from labelu.internal.common.config import settings

# 开训前训练 GPU 至少需保留的空闲显存占比（相对该卡总显存）
_DEFAULT_MIN_FREE_VRAM_FRACTION = 0.15
# 绝对下限（MB），避免小显存卡算出过小阈值
_MIN_FREE_VRAM_FLOOR_MB = 2048
# 资源画像缓存 TTL（秒），避免每次排队检查都调 nvidia-smi
_PROFILE_TTL_SECONDS = 45.0
# 排队看门狗：有排队时更频繁扫描
_QUEUE_SCAN_BUSY_SECONDS = 5
_QUEUE_SCAN_IDLE_SECONDS = 20
_MIN_CPU_CORES_PER_TRAIN_JOB = 6

_profile_lock = threading.Lock()
_profile_cache: Optional["YoloTrainResourceProfile"] = None
_profile_cached_at: float = 0.0


@dataclass(frozen=True)
class GpuMemorySnapshot:
    index: int
    total_mb: int
    free_mb: int


@dataclass(frozen=True)
class YoloTrainResourceProfile:
    """由探测结果推导的训练资源策略。"""

    train_gpu_indices: Tuple[int, ...]
    gpus_per_job: int
    max_concurrent_jobs: int
    min_free_mb_per_gpu: int
    queue_scan_interval_seconds: int
    cpu_only: bool

    def summary(self) -> str:
        if self.cpu_only:
            return "cpu_only max_concurrent=1"
        return (
            f"train_gpus={list(self.train_gpu_indices)} "
            f"gpus_per_job={self.gpus_per_job} "
            f"max_concurrent={self.max_concurrent_jobs} "
            f"min_free_mb={self.min_free_mb_per_gpu} "
            f"queue_scan={self.queue_scan_interval_seconds}s"
        )


def _query_nvidia_gpu_memory() -> List[GpuMemorySnapshot]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out: List[GpuMemorySnapshot] = []
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            out.append(
                GpuMemorySnapshot(
                    index=int(parts[0]),
                    total_mb=int(float(parts[1])),
                    free_mb=int(float(parts[2])),
                )
            )
        except ValueError:
            continue
    return out


def _train_gpu_indices() -> List[int]:
    from labelu.internal.application.service.yolo_train import (
        _cuda_runtime_snapshot,
        _requested_gpu_indices,
        _resolve_yolo_train_device,
        _is_cpu_train_device,
    )

    explicit = (settings.YOLO_TRAIN_DEVICE or "").strip()
    if explicit:
        if _is_cpu_train_device(explicit):
            return []
        try:
            return _requested_gpu_indices(explicit)
        except ValueError:
            return []
    device = _resolve_yolo_train_device()
    if _is_cpu_train_device(device):
        return []
    try:
        return _requested_gpu_indices(device)
    except ValueError:
        return []


def _min_free_vram_fraction() -> float:
    raw = float(getattr(settings, "YOLO_TRAIN_GPU_MIN_FREE_FRACTION", _DEFAULT_MIN_FREE_VRAM_FRACTION) or _DEFAULT_MIN_FREE_VRAM_FRACTION)
    return max(0.05, min(0.95, raw))


def _min_free_mb_for_gpu(total_mb: int) -> int:
    if total_mb <= 0:
        return _MIN_FREE_VRAM_FLOOR_MB
    by_fraction = int(total_mb * _min_free_vram_fraction())
    return max(_MIN_FREE_VRAM_FLOOR_MB, by_fraction)


def _host_cpu_count() -> int:
    return max(1, int(os.cpu_count() or 1))


def _query_host_available_memory_mb() -> Optional[int]:
    """Linux MemAvailable；其他平台返回 None（跳过内存门禁）。"""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return max(0, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _query_host_cpu_load_ratio() -> Optional[float]:
    """1 分钟 load average / CPU 核数；非 Linux 返回 None。"""
    try:
        with open("/proc/loadavg", encoding="utf-8") as handle:
            load_1m = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return max(0.0, load_1m / float(_host_cpu_count()))


def _memory_concurrency_cap(
    *, available_mb: Optional[int], reserve_mb: int, per_job_mb: int
) -> Optional[int]:
    """按主机可用内存推导可再并发的作业数；内存未知时不做限制。"""
    if available_mb is None:
        return None
    reserve = max(0, int(reserve_mb or 0))
    per_job = max(512, int(per_job_mb or 4096))
    return max(1, max(0, int(available_mb) - reserve) // per_job)


def yolo_train_host_resources_available() -> Tuple[bool, str]:
    """开训前的主机内存/CPU 负载门禁，避免并发训练压垮 Web/数据库。"""
    available_mb = _query_host_available_memory_mb()
    reserve_mb = int(getattr(settings, "YOLO_TRAIN_MIN_FREE_MEMORY_MB", 8192) or 0)
    if available_mb is not None and available_mb < reserve_mb:
        return (
            False,
            f"主机可用内存 {available_mb}MB < 保留阈值 {reserve_mb}MB",
        )
    load_ratio = _query_host_cpu_load_ratio()
    max_ratio = float(getattr(settings, "YOLO_TRAIN_MAX_CPU_LOAD_RATIO", 0.9) or 0.9)
    if load_ratio is not None and load_ratio > max_ratio:
        return (
            False,
            f"主机 CPU 负载 {load_ratio:.2f} > 阈值 {max_ratio:.2f}",
        )
    return True, ""


def compute_yolo_train_resource_profile(
    *, queue_has_waiters: bool = False
) -> YoloTrainResourceProfile:
    """
    自动推导排队策略：
    - max_concurrent：YOLO_TRAIN_GPU_AUTO_ALLOC 开启且多卡时等于 GPU 数；否则固定 device 多卡时为 1。
      CPU 训练同样为 1。
    - min_free_mb：按各训练卡总显存的 35%（不低于 2GB）作为开训门槛。
    - queue_scan：有排队 15s，空闲 45s。
    """
    indices = _train_gpu_indices()
    scan_interval = (
        _QUEUE_SCAN_BUSY_SECONDS if queue_has_waiters else _QUEUE_SCAN_IDLE_SECONDS
    )

    if not indices:
        return YoloTrainResourceProfile(
            train_gpu_indices=(),
            gpus_per_job=0,
            max_concurrent_jobs=1,
            min_free_mb_per_gpu=0,
            queue_scan_interval_seconds=scan_interval,
            cpu_only=True,
        )

    gpu_rows = _query_nvidia_gpu_memory()
    gpu_map = {row.index: row for row in gpu_rows}
    per_gpu_min: List[int] = []
    for idx in indices:
        row = gpu_map.get(idx)
        if row is None:
            per_gpu_min.append(_MIN_FREE_VRAM_FLOOR_MB)
        else:
            per_gpu_min.append(_min_free_mb_for_gpu(row.total_mb))

    from labelu.internal.common.yolo_train_gpu_allocator import (
        yolo_train_gpu_auto_alloc_enabled,
    )

    if yolo_train_gpu_auto_alloc_enabled():
        # 自动分配：小任务各占一卡，大任务占满训练池；并发上限为 GPU 数量（可配置抬高）。
        gpus_per_job = 1
        auto_cap = max(1, len(indices))
        cpu_cap = max(1, _host_cpu_count() // _MIN_CPU_CORES_PER_TRAIN_JOB)
        cfg_cap = int(getattr(settings, "YOLO_TRAIN_MAX_CONCURRENT_JOBS", 0) or 0)
        max_concurrent = min(auto_cap, cpu_cap)
        if cfg_cap > 0:
            max_concurrent = min(max_concurrent, cfg_cap)
        memory_cap = _memory_concurrency_cap(
            available_mb=_query_host_available_memory_mb(),
            reserve_mb=int(
                getattr(settings, "YOLO_TRAIN_MIN_FREE_MEMORY_MB", 8192) or 0
            ),
            per_job_mb=int(
                getattr(settings, "YOLO_TRAIN_MEMORY_PER_JOB_MB", 4096) or 4096
            ),
        )
        if memory_cap is not None:
            max_concurrent = max(1, min(max_concurrent, memory_cap))
    else:
        gpus_per_job = max(1, len(indices))
        # 固定 device（如 0,1 DDP）：所有作业共用同一配置，并发槽位为 1。
        max_concurrent = 1 if gpus_per_job > 1 else max(1, len(indices))
    min_free = min(per_gpu_min) if per_gpu_min else _MIN_FREE_VRAM_FLOOR_MB

    return YoloTrainResourceProfile(
        train_gpu_indices=tuple(indices),
        gpus_per_job=gpus_per_job,
        max_concurrent_jobs=max_concurrent,
        min_free_mb_per_gpu=min_free,
        queue_scan_interval_seconds=scan_interval,
        cpu_only=False,
    )


def get_yolo_train_resource_profile(
    *, queue_has_waiters: bool = False, force_refresh: bool = False
) -> YoloTrainResourceProfile:
    global _profile_cache, _profile_cached_at
    now = time.monotonic()
    with _profile_lock:
        stale = (now - _profile_cached_at) >= _PROFILE_TTL_SECONDS
        if force_refresh or _profile_cache is None or stale:
            profile = compute_yolo_train_resource_profile(
                queue_has_waiters=queue_has_waiters
            )
            _profile_cache = profile
            _profile_cached_at = now
            return profile
        if queue_has_waiters and _profile_cache.queue_scan_interval_seconds != _QUEUE_SCAN_BUSY_SECONDS:
            profile = compute_yolo_train_resource_profile(queue_has_waiters=True)
            _profile_cache = profile
            _profile_cached_at = now
            return profile
        return _profile_cache


def invalidate_yolo_train_resource_profile_cache() -> None:
    global _profile_cache, _profile_cached_at
    with _profile_lock:
        _profile_cache = None
        _profile_cached_at = 0.0


def log_yolo_train_resource_profile_once() -> YoloTrainResourceProfile:
    profile = get_yolo_train_resource_profile(force_refresh=True)
    logger.info("YOLO train resource profile (auto): {}", profile.summary())
    return profile
