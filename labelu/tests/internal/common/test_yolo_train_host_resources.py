"""Host resource gates used by the YOLO training scheduler."""

from __future__ import annotations

import pytest

from labelu.internal.common import yolo_train_resource_profile as profile
from labelu.internal.common.config import settings


def test_memory_concurrency_cap_reserves_headroom():
    assert profile._memory_concurrency_cap(
        available_mb=20_000, reserve_mb=8_000, per_job_mb=4_000
    ) == 3
    assert profile._memory_concurrency_cap(
        available_mb=8_000, reserve_mb=8_000, per_job_mb=4_000
    ) == 1


def test_host_resource_gate_rejects_low_memory(monkeypatch):
    monkeypatch.setattr(profile, "_query_host_available_memory_mb", lambda: 4096)
    monkeypatch.setattr(profile, "_query_host_cpu_load_ratio", lambda: 0.1)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MIN_FREE_MEMORY_MB", 8192)

    ok, reason = profile.yolo_train_host_resources_available()

    assert ok is False
    assert "可用内存" in reason


def test_host_resource_gate_rejects_high_cpu_load(monkeypatch):
    monkeypatch.setattr(profile, "_query_host_available_memory_mb", lambda: 64_000)
    monkeypatch.setattr(profile, "_query_host_cpu_load_ratio", lambda: 0.95)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MIN_FREE_MEMORY_MB", 8192)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MAX_CPU_LOAD_RATIO", 0.9)

    ok, reason = profile.yolo_train_host_resources_available()

    assert ok is False
    assert "CPU" in reason


def test_host_resource_gate_allows_normal_load(monkeypatch):
    monkeypatch.setattr(profile, "_query_host_available_memory_mb", lambda: 64_000)
    monkeypatch.setattr(profile, "_query_host_cpu_load_ratio", lambda: 0.2)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MIN_FREE_MEMORY_MB", 8192)

    assert profile.yolo_train_host_resources_available() == (True, "")


def test_auto_profile_caps_concurrency_by_memory(monkeypatch):
    monkeypatch.setattr(profile, "_train_gpu_indices", lambda: [0, 1])
    monkeypatch.setattr(
        profile,
        "_query_nvidia_gpu_memory",
        lambda: [
            profile.GpuMemorySnapshot(index=0, total_mb=23000, free_mb=22000),
            profile.GpuMemorySnapshot(index=1, total_mb=23000, free_mb=22000),
        ],
    )
    monkeypatch.setattr(profile, "_query_host_available_memory_mb", lambda: 16_000)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MIN_FREE_MEMORY_MB", 8_000)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MEMORY_PER_JOB_MB", 4_000)
    monkeypatch.setattr(settings, "YOLO_TRAIN_MAX_CONCURRENT_JOBS", 0)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.yolo_train_gpu_auto_alloc_enabled",
        lambda: True,
    )

    result = profile.compute_yolo_train_resource_profile()

    assert result.max_concurrent_jobs == 2
