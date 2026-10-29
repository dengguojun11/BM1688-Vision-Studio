"""YOLO 超参元数据：平台默认 vs Ultralytics 官方默认。"""
from labelu.internal.common.yolo_train_hyperparams import (
    YOLO_TRAIN_FIELD_DEFAULTS,
    get_yolo_official_field_defaults,
    hyperparam_field_meta,
    official_default_for_form,
)


def test_official_defaults_cover_allowed_keys():
    official = get_yolo_official_field_defaults()
    for key in (
        "batch",
        "patience",
        "lr0",
        "mosaic",
        "max_det",
    ):
        assert key in official


def test_platform_differs_from_official_on_optional_none_keys():
    official = get_yolo_official_field_defaults()
    assert official.get("conf") is None
    assert YOLO_TRAIN_FIELD_DEFAULTS["conf"] == 0.25
    assert official.get("freeze") is None
    assert YOLO_TRAIN_FIELD_DEFAULTS["freeze"] == 0


def test_hyperparam_field_meta_includes_both_defaults():
    rows = hyperparam_field_meta()
    batch = next(r for r in rows if r["key"] == "batch")
    assert batch["default"] == 16
    assert batch["official_default"] == 16
    conf = next(r for r in rows if r["key"] == "conf")
    assert conf["default"] == 0.25
    assert conf["official_default"] is None
    workers = next(r for r in rows if r["key"] == "workers")
    assert workers["default"] == 8
    assert "单 GPU 按填写值生效" in workers["hint"]


def test_official_default_for_form_maps_none():
    assert official_default_for_form("freeze", None) == 0
    assert official_default_for_form("conf", None) is None


def test_merge_iteration_param_dicts_later_layer_wins():
    from labelu.internal.common.yolo_train_hyperparams import merge_iteration_param_dicts

    full = {"cls": 0.5, "mosaic": 0.3, "lr0": 0.01}
    nxt = {"mosaic": 0.7, "epochs": 200, "imgsz": 1280, "batch": 16, "notes": "skip"}
    merged = merge_iteration_param_dicts(full, nxt)
    assert merged["cls"] == 0.5
    assert merged["mosaic"] == 0.7
    assert merged["epochs"] == 200
    assert merged["imgsz"] == 1280
    assert "notes" not in merged


def test_automatic_iteration_patience_uses_safe_epoch_based_floor():
    from labelu.internal.common.yolo_train_hyperparams import (
        default_train_patience,
        protect_automatic_iteration_patience,
    )

    assert default_train_patience(80) == 30
    assert default_train_patience(100) == 30
    assert default_train_patience(200) == 60
    assert protect_automatic_iteration_patience({"patience": 10}, epochs=100)[
        "patience"
    ] == 30
    assert protect_automatic_iteration_patience({"patience": 80}, epochs=100)[
        "patience"
    ] == 80
