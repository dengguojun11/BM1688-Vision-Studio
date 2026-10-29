"""平台仅允许 yolo11n.pt 官方预训练。"""
import pytest

from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.service.yolo_train import (
    PLATFORM_PRESET_WEIGHT,
    _model_weights_field_for_storage,
    _normalize_preset_model_weights,
)
from labelu.internal.common.error_code import LabelUException


def test_normalize_preset_accepts_yolo11n():
    assert _normalize_preset_model_weights("yolo11n.pt") == PLATFORM_PRESET_WEIGHT
    assert _normalize_preset_model_weights("YOLO11n.PT") == PLATFORM_PRESET_WEIGHT


def test_normalize_preset_rejects_yolo11s():
    with pytest.raises(LabelUException) as exc:
        _normalize_preset_model_weights("yolo11s.pt")
    assert "yolo11n.pt" in str(exc.value.msg)


def test_model_weights_field_for_storage_preset():
    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt", weights_source="preset")
    assert _model_weights_field_for_storage(cmd) == PLATFORM_PRESET_WEIGHT
