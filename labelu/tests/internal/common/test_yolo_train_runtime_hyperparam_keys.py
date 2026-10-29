from labelu.internal.common.yolo_train_hyperparams import (
    RUNTIME_EDITABLE_TRAIN_HYPER_KEYS,
    normalize_runtime_editable_hyperparams,
)


def test_normalize_runtime_editable_hyperparams_keeps_safe_runtime_keys():
    out = normalize_runtime_editable_hyperparams(
        {
            "batch": "6",
            "imgsz": "1280",
            "multi_scale": "false",
            "amp": "true",
            "workers": "4",
            "mosaic": "0.3",
            "optimizer": "SGD",
            "data": "bad.yaml",
        }
    )

    assert out == {
        "batch": 6,
        "imgsz": 1280,
        "multi_scale": False,
        "amp": True,
        "workers": 4,
        "mosaic": 0.3,
    }


def test_runtime_editable_key_set_orders_resource_controls_first():
    keys = list(RUNTIME_EDITABLE_TRAIN_HYPER_KEYS)

    assert keys[:6] == ["batch", "imgsz", "multi_scale", "amp", "workers", "patience"]
