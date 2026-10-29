from __future__ import annotations

from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common.config import settings
from labelu.internal.common.yolo_train_gpu_allocator import (
    build_gpu_occupancy,
    choose_single_gpu_device,
    classify_job_gpu_tier,
    estimate_job_load_score,
    gpu_display_fields_for_job,
    job_needs_dual_gpu,
    plan_gpu_allocation,
    release_job_gpu_allocation,
    register_job_gpu_allocation,
    try_allocate_gpu_for_job,
)


def test_small_yolo11n_stays_single_gpu_even_when_prefer_dual_enabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_DEVICE", "0,1")
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt", imgsz=640)
    score = estimate_job_load_score(cmd)
    assert score < 4.0
    assert classify_job_gpu_tier(cmd) == "small"
    assert job_needs_dual_gpu(cmd) is False


def test_small_yolo11n_single_when_prefer_dual_disabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", False)
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_DEVICE", "0,1")
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt", imgsz=640)
    assert job_needs_dual_gpu(cmd) is False


def test_large_yolo11l_with_big_batch_needs_dual(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_DEVICE", "0,1")
    cmd = CreateYoloTrainJobCommand(
        model_weights="yolo11l.pt",
        imgsz=640,
        hyperparams={"batch": 32},
    )
    assert classify_job_gpu_tier(cmd) == "large"
    assert job_needs_dual_gpu(cmd) is True


def test_large_imgsz_forces_single_gpu_even_for_x_model():
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11x.pt", imgsz=1024)
    assert classify_job_gpu_tier(cmd) == "medium"
    assert job_needs_dual_gpu(cmd) is False


def test_plan_two_small_jobs_on_different_gpus(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", False)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.train_gpu_pool_indices",
        lambda: (0, 1),
    )
    pool = {0: None, 1: None}
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt", imgsz=640)
    p1 = plan_gpu_allocation(
        cmd, occupied_by_gpu=dict(pool), free_mb={0: 20000, 1: 20000}, min_free_mb=8000
    )
    assert p1 is not None
    assert p1.device in ("0", "1")
    assert p1.tier == "small"

    occupied = dict(pool)
    occupied[int(p1.device)] = 101
    p2 = plan_gpu_allocation(
        cmd, occupied_by_gpu=occupied, free_mb={0: 20000, 1: 20000}, min_free_mb=8000
    )
    assert p2 is not None
    assert p2.device != p1.device


def test_plan_prefers_freer_gpu_for_single_gpu_job(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", True)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.train_gpu_pool_indices",
        lambda: (0, 1),
    )
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11m.pt", imgsz=1024)
    plan = plan_gpu_allocation(
        cmd,
        occupied_by_gpu={0: None, 1: None},
        free_mb={0: 12000, 1: 22000},
        min_free_mb=8000,
    )
    assert plan is not None
    assert plan.device == "1"
    assert plan.tier == "medium"


def test_plan_dual_job_blocks_when_one_gpu_busy():
    cmd = CreateYoloTrainJobCommand(
        model_weights="yolo11l.pt", imgsz=640, hyperparams={"batch": 32}
    )
    plan = plan_gpu_allocation(
        cmd,
        occupied_by_gpu={0: 99, 1: None},
        free_mb={0: 20000, 1: 20000},
        min_free_mb=8000,
    )
    assert plan is None


def test_try_allocate_registers_and_release(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", False)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.train_gpu_pool_indices",
        lambda: (0, 1),
    )
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt", imgsz=640)
    plan = try_allocate_gpu_for_job(
        501,
        cmd,
        occupied_by_gpu={0: None, 1: None},
        free_mb={0: 20000, 1: 20000},
        min_free_mb=8000,
    )
    assert plan is not None
    assert plan.device in ("0", "1")
    assert plan.tier == "small"
    occ = build_gpu_occupancy()
    assert occ.get(int(plan.device)) == 501
    release_job_gpu_allocation(501)
    occ2 = build_gpu_occupancy()
    assert occ2.get(0) is None
    assert occ2.get(1) is None


def test_large_job_avoids_dual_when_queue_has_waiters(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_DEVICE", "0,1")
    cmd = CreateYoloTrainJobCommand(
        model_weights="yolo11l.pt",
        imgsz=640,
        hyperparams={"batch": 32},
    )
    assert job_needs_dual_gpu(cmd, queued_job_count=1) is False


def test_choose_single_gpu_device_prefers_free_and_idle_gpu(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.train_gpu_pool_indices",
        lambda: (0, 1),
    )
    chosen = choose_single_gpu_device(
        candidate_indices=(0, 1),
        occupied_by_gpu={0: 100, 1: None},
        free_mb={0: 23000, 1: 16000},
    )
    assert chosen == "1"


def test_gpu_display_fields_from_allocation_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    job_dir = tmp_path / "yolo_train" / "job-601"
    job_dir.mkdir(parents=True)
    (job_dir / "gpu_allocation.json").write_text(
        '{"device": "0,1", "gpu_indices": [0, 1], "tier": "large", "load_score": 5.0, "reason": "test"}',
        encoding="utf-8",
    )
    fields = gpu_display_fields_for_job(601)
    assert fields["train_gpu_mode"] == "dual"
    assert fields["train_gpu_label"] == "双卡 DDP (0,1)"
    assert fields["train_gpu_device"] == "0,1"


def test_gpu_display_fields_from_args_yaml_fallback(monkeypatch, tmp_path):
    from labelu.internal.common.yolo_train_gpu_allocator import gpu_display_fields_for_job

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    train_dir = tmp_path / "yolo_train" / "job-602" / "train"
    train_dir.mkdir(parents=True)
    (train_dir / "args.yaml").write_text("device: '1'\namp: false\n", encoding="utf-8")
    fields = gpu_display_fields_for_job(602)
    assert fields["train_gpu_mode"] == "single"
    assert fields["train_gpu_label"] == "单卡 GPU1"
    monkeypatch.setattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.train_gpu_pool_indices",
        lambda: (0,),
    )
    from labelu.internal.common.yolo_train_gpu_allocator import (
        yolo_train_gpu_auto_alloc_enabled,
    )

    assert yolo_train_gpu_auto_alloc_enabled() is False
