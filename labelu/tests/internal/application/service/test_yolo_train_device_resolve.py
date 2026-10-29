"""YOLO 训练 device 解析：显式多卡配置须在 CUDA 可用时才生效。"""
from unittest.mock import patch

from labelu.internal.application.service import yolo_train as yt


def test_resolve_auto_cpu_when_cuda_unavailable():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", ""):
        with patch.object(yt, "_cuda_runtime_snapshot", return_value=(False, 2)):
            assert yt._resolve_yolo_train_device() == "cpu"


def test_resolve_auto_gpu0_when_cuda_available():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", ""):
        with patch.object(yt, "_cuda_runtime_snapshot", return_value=(True, 2)):
            assert yt._resolve_yolo_train_device() == "0"


def test_explicit_multi_gpu_fallback_cpu_when_cuda_unavailable():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", "0,1"):
        with patch.object(yt, "_cuda_runtime_snapshot", return_value=(False, 2)):
            assert yt._resolve_yolo_train_device() == "cpu"


def test_explicit_multi_gpu_kept_when_cuda_available():
    with patch.object(yt.settings, "YOLO_TRAIN_DEVICE", "0,1"):
        with patch.object(yt, "_cuda_runtime_snapshot", return_value=(True, 2)):
            assert yt._resolve_yolo_train_device() == "0,1"


def test_prepare_cuda_env_removes_empty_visible_devices():
    with patch.dict("os.environ", {"CUDA_VISIBLE_DEVICES": ""}, clear=False):
        yt.prepare_cuda_env_for_yolo_train()
        assert "CUDA_VISIBLE_DEVICES" not in __import__("os").environ
