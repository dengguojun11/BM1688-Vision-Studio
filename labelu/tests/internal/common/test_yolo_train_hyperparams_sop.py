"""54.99 SOP 超参基线与白名单扩展。"""
from labelu.internal.common.yolo_train_hyperparams import (
    GARBAGE_EXPOSURE_RECT_PRESET_SLUG,
    GARBAGE_EXPOSURE_SOP_HYPERPARAMS,
    get_iteration_baseline_hyperparams,
    normalize_hyperparams,
)


def test_sop_baseline_includes_loss_and_augment_keys():
    hp = get_iteration_baseline_hyperparams(GARBAGE_EXPOSURE_RECT_PRESET_SLUG)
    assert hp["batch"] == 4
    assert hp["patience"] == 30
    assert hp["workers"] == 8
    assert hp["amp"] is True
    assert hp["optimizer"] == "AdamW"
    assert hp["cos_lr"] is True
    assert hp["multi_scale"] is True
    assert hp["cutmix"] == 0.05
    assert hp["box"] == 5.5
    assert hp["cls"] == 1.0
    assert hp["dfl"] == 2.0
    assert hp["max_det"] == 800


def test_normalize_accepts_new_ultralytics_keys():
    raw = {
        "cutmix": 0.05,
        "multi_scale": True,
        "box": 5.5,
        "cls": 1.0,
        "dfl": 2.0,
        "save_period": 10,
    }
    out = normalize_hyperparams(raw)
    assert out["cutmix"] == 0.05
    assert out["multi_scale"] is True
    assert out["box"] == 5.5
    assert "save_period" in out


def test_unknown_preset_returns_empty_baseline():
    assert get_iteration_baseline_hyperparams("unknownPreset") == {}


def test_sop_dict_matches_normalized_baseline():
    assert (
        normalize_hyperparams(GARBAGE_EXPOSURE_SOP_HYPERPARAMS)
        == get_iteration_baseline_hyperparams(GARBAGE_EXPOSURE_RECT_PRESET_SLUG)
    )
