from __future__ import annotations

from labelu.internal.common.yolo_train_resource_profile import (
    GpuMemorySnapshot,
    compute_yolo_train_resource_profile,
    get_yolo_train_resource_profile,
    invalidate_yolo_train_resource_profile_cache,
)


def test_profile_cache_refreshes_scan_interval_when_queue_busy(monkeypatch):
    invalidate_yolo_train_resource_profile_cache()
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [],
    )

    idle = get_yolo_train_resource_profile(queue_has_waiters=False, force_refresh=True)
    assert idle.queue_scan_interval_seconds == 20

    busy = get_yolo_train_resource_profile(queue_has_waiters=True)
    assert busy.queue_scan_interval_seconds == 5


def test_profile_uses_max_gpu_min_free_across_train_devices(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [0, 1],
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._query_nvidia_gpu_memory",
        lambda: [
            GpuMemorySnapshot(index=0, total_mb=8192, free_mb=6000),
            GpuMemorySnapshot(index=1, total_mb=24576, free_mb=20000),
        ],
    )
    profile = compute_yolo_train_resource_profile()
    assert profile.min_free_mb_per_gpu == 2048


def test_compute_profile_legacy_dual_gpu_serial(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", False)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [0, 1],
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._query_nvidia_gpu_memory",
        lambda: [
            GpuMemorySnapshot(index=0, total_mb=24576, free_mb=20000),
            GpuMemorySnapshot(index=1, total_mb=24576, free_mb=20000),
        ],
    )
    profile = compute_yolo_train_resource_profile(queue_has_waiters=True)
    assert profile.max_concurrent_jobs == 1
    assert profile.gpus_per_job == 2


def test_compute_profile_auto_alloc_parallel_slots(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.yolo_train_gpu_auto_alloc_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [0, 1],
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._query_nvidia_gpu_memory",
        lambda: [
            GpuMemorySnapshot(index=0, total_mb=24576, free_mb=20000),
            GpuMemorySnapshot(index=1, total_mb=24576, free_mb=20000),
        ],
    )
    profile = compute_yolo_train_resource_profile()
    assert profile.max_concurrent_jobs == 2
    assert profile.gpus_per_job == 1


def test_compute_profile_auto_alloc_limited_by_cpu_budget(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.yolo_train_gpu_auto_alloc_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [0, 1],
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._host_cpu_count",
        lambda: 8,
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._query_nvidia_gpu_memory",
        lambda: [
            GpuMemorySnapshot(index=0, total_mb=24576, free_mb=20000),
            GpuMemorySnapshot(index=1, total_mb=24576, free_mb=20000),
        ],
    )
    profile = compute_yolo_train_resource_profile()
    assert profile.max_concurrent_jobs == 1
    assert profile.gpus_per_job == 1
