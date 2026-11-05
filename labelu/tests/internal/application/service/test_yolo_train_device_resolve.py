"""YOLO 训练 device 解析：禁止 CPU 训练时须失败关闭。"""
from unittest.mock import patch

import pytest

from labelu.internal.application.service import yolo_train as yt
from labelu.internal.common.error_code import ErrorCode, LabelUException


def _patch_no_gpu():
    return patch.object(yt, "_cuda_runtime_snapshot", return_value=(False, 2))


def test_resolve_auto_raises_when_gpu_required_and_cuda_unavailable():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", ""):
        with patch.object(yt.settings, "YOLO_TRAIN_REQUIRE_GPU", True):
            with _patch_no_gpu():
                with pytest.raises(LabelUException) as exc:
                    yt._resolve_yolo_train_device()
    assert exc.value.code == ErrorCode.CODE_57034_YOLO_TRAIN_GPU_REQUIRED.value[0]


def test_resolve_auto_gpu0_when_cuda_available():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", ""):
        with patch.object(yt, "_cuda_runtime_snapshot", return_value=(True, 2)):
            assert yt._resolve_yolo_train_device() == "0"


def test_explicit_gpu_raises_when_cuda_unavailable_and_gpu_required():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", "0,1"):
        with patch.object(yt.settings, "YOLO_TRAIN_REQUIRE_GPU", True):
            with _patch_no_gpu():
                with pytest.raises(LabelUException):
                    yt._resolve_yolo_train_device()


def test_explicit_multi_gpu_kept_when_cuda_available():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", "0,1"):
        with patch.object(yt, "_cuda_runtime_snapshot", return_value=(True, 2)):
            assert yt._resolve_yolo_train_device() == "0,1"


def test_cpu_device_rejected_when_gpu_required():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", "cpu"):
        with patch.object(yt.settings, "YOLO_TRAIN_REQUIRE_GPU", True):
            with pytest.raises(LabelUException):
                yt._resolve_yolo_train_device()


def test_cpu_fallback_allowed_when_gpu_requirement_disabled():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", ""):
        with patch.object(yt.settings, "YOLO_TRAIN_REQUIRE_GPU", False):
            with _patch_no_gpu():
                assert yt._resolve_yolo_train_device() == "cpu"


def test_prepare_cuda_env_removes_empty_visible_devices():
    with patch.dict("os.environ", {"CUDA_VISIBLE_DEVICES": ""}, clear=False):
        yt.prepare_cuda_env_for_yolo_train()
        assert "CUDA_VISIBLE_DEVICES" not in __import__("os").environ


def test_cpu_child_binding_does_not_poison_worker_cuda_env(monkeypatch):
    import os

    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert yolo_train_queue._bind_train_child_cuda_device("cpu") == "cpu"
    assert "CUDA_VISIBLE_DEVICES" not in os.environ


def test_gpu_probe_falls_back_to_nvidia_smi_when_torch_cuda_is_poisoned(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    with patch.object(yt, "_cuda_runtime_snapshot", return_value=(False, 0)):
        with patch.object(
            yt.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="0\n1\n"),
        ):
            assert yt.gpu_training_available() == (True, 1)


def test_gpu_probe_reports_no_gpu_when_driver_lists_none(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    with patch.object(yt, "_cuda_runtime_snapshot", return_value=(False, 0)):
        with patch.object(
            yt.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=1, stdout=""),
        ):
            assert yt.gpu_training_available() == (False, 0)
