"""多卡训练 spawn 子进程与默认 workers/amp。"""
from labelu.internal.application.service.yolo_train import (
    _default_train_runtime_kwargs,
    _is_multi_gpu_device,
    _should_spawn_train_process,
)


def test_should_spawn_train_process_multi_gpu(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._resolve_yolo_train_device",
        lambda: "0,1",
    )
    assert _should_spawn_train_process(imgsz=640) is True


def test_should_spawn_train_process_single_gpu(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._resolve_yolo_train_device",
        lambda: "0",
    )
    assert _should_spawn_train_process(imgsz=640) is False


def test_default_train_runtime_kwargs(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 0)
    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", False)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.os.cpu_count",
        lambda: 4,
    )
    kw = _default_train_runtime_kwargs(train_device="0", imgsz=640)
    assert kw == {"workers": 0, "amp": False, "cache": False}


def test_default_train_runtime_caps_configured_workers(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 12)
    kw = _default_train_runtime_kwargs(train_device="0", imgsz=640)
    assert kw["workers"] == 8
    assert kw["cache"] is False


def test_is_multi_gpu_device():
    assert _is_multi_gpu_device("0,1") is True
    assert _is_multi_gpu_device("0") is False
