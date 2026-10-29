import json

from PIL import Image

from labelu.internal.application.service.yolo_dataset import (
    build_training_rect_class_names,
    prepare_yolo_dataset,
    validate_yolo_training_prerequisites,
)
from labelu.internal.common.task_annotation_completeness import audit_one_sample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample


def _garbage_street_config():
    return {
        "defaultRectPresetId": "garbageStreet",
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {"key": "垃圾", "value": "garbage"},
                        {"key": "非垃圾", "value": "non_garbage"},
                    ]
                },
            }
        ],
    }


def _sample_result(rects):
    return {
        "width": 100,
        "height": 80,
        "rectTool": {"result": rects},
    }


def _task_sample(sample_id: int, filename: str, result: dict) -> TaskSample:
    attachment = TaskAttachment()
    attachment.id = sample_id
    attachment.filename = filename
    attachment.path = f"/upload/{filename}"

    sample = TaskSample()
    sample.id = sample_id
    sample.task_id = 1
    sample.state = SampleState.DONE.value
    sample.file = attachment
    sample.data = json.dumps(
        {"result": json.dumps(result, ensure_ascii=False)},
        ensure_ascii=False,
    )
    return sample


def test_build_training_rect_class_names_keeps_non_garbage_as_detection_class():
    cfg = _garbage_street_config()
    assert build_training_rect_class_names(cfg) == ["garbage", "non_garbage"]

    other_cfg = dict(cfg)
    other_cfg["defaultRectPresetId"] = "otherPreset"
    assert build_training_rect_class_names(other_cfg) == ["garbage", "non_garbage"]


def test_audit_one_sample_allows_empty_rect_as_background_for_garbage_street():
    sample = _task_sample(1, "bg.jpg", _sample_result([]))
    row = audit_one_sample(sample, _garbage_street_config())
    assert row["rect_ok"] is True
    assert row["ok"] is True
    assert row["sample_quality_state"] == SampleState.DONE.value


def test_audit_one_sample_rejects_zero_area_rect():
    sample = _task_sample(
        1,
        "bad.jpg",
        _sample_result([{"x": 0, "y": 0, "width": 0, "height": 0, "label": "garbage"}]),
    )
    row = audit_one_sample(sample, _garbage_street_config())
    assert row["geometry_ok"] is False
    assert row["geometry_error"] == "rect out of bounds"


def test_prepare_yolo_dataset_keeps_background_negative_samples(tmp_path):
    media_root = tmp_path / "media"
    upload_dir = media_root / "upload"
    upload_dir.mkdir(parents=True)
    for name in ("bg.jpg", "pos.jpg"):
        Image.new("RGB", (100, 80), color="white").save(upload_dir / name)

    background = _task_sample(1, "bg.jpg", _sample_result([]))
    positive = _task_sample(
        2,
        "pos.jpg",
        _sample_result(
            [{"x": 10, "y": 10, "width": 20, "height": 12, "label": "garbage"}]
        ),
    )

    train_n, val_n, test_n, classes, _ = prepare_yolo_dataset(
        task_config=_garbage_street_config(),
        samples=[background, positive],
        media_root=media_root,
        dataset_root=tmp_path / "dataset",
        val_ratio=0.5,
        test_ratio=0.0,
        seed=42,
    )

    assert (train_n, val_n, test_n) == (1, 1, 0)
    assert classes == ["garbage", "non_garbage"]

    label_files = list((tmp_path / "dataset" / "labels").rglob("*.txt"))
    assert len(label_files) == 2
    assert any(path.read_text(encoding="utf-8") == "" for path in label_files)
    assert any("0 " in path.read_text(encoding="utf-8") for path in label_files)


def test_prepare_yolo_dataset_exports_non_garbage_boxes_as_class(tmp_path):
    media_root = tmp_path / "media"
    upload_dir = media_root / "upload"
    upload_dir.mkdir(parents=True)
    for name in ("garbage.jpg", "non-garbage.jpg"):
        Image.new("RGB", (100, 80), color="white").save(upload_dir / name)

    garbage = _task_sample(
        1,
        "garbage.jpg",
        _sample_result(
            [{"x": 10, "y": 10, "width": 20, "height": 12, "label": "garbage"}]
        ),
    )
    non_garbage = _task_sample(
        2,
        "non-garbage.jpg",
        _sample_result(
            [
                {
                    "x": 20,
                    "y": 16,
                    "width": 18,
                    "height": 10,
                    "label": "non_garbage",
                }
            ]
        ),
    )

    _, _, _, classes, _ = prepare_yolo_dataset(
        task_config=_garbage_street_config(),
        samples=[garbage, non_garbage],
        media_root=media_root,
        dataset_root=tmp_path / "dataset",
        val_ratio=0.5,
        test_ratio=0.0,
        seed=42,
    )

    assert classes == ["garbage", "non_garbage"]
    label_texts = [
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "dataset" / "labels").rglob("*.txt")
    ]
    assert all(text.strip() for text in label_texts)
    assert any(text.startswith("1 ") for text in label_texts)


def test_prepare_yolo_dataset_removes_previous_generated_files(tmp_path):
    media_root = tmp_path / "media"
    upload_dir = media_root / "upload"
    upload_dir.mkdir(parents=True)
    for name in ("bg.jpg", "pos.jpg"):
        Image.new("RGB", (100, 80), color="white").save(upload_dir / name)

    dataset_root = tmp_path / "dataset"
    for stale_path in (
        dataset_root / "images" / "train" / "stale.jpg",
        dataset_root / "labels" / "val" / "stale.txt",
        dataset_root / "labelsxml" / "test" / "stale.xml",
    ):
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text("stale", encoding="utf-8")

    prepare_yolo_dataset(
        task_config=_garbage_street_config(),
        samples=[
            _task_sample(1, "bg.jpg", _sample_result([])),
            _task_sample(
                2,
                "pos.jpg",
                _sample_result(
                    [
                        {
                            "x": 10,
                            "y": 10,
                            "width": 20,
                            "height": 12,
                            "label": "garbage",
                        }
                    ]
                ),
            ),
        ],
        media_root=media_root,
        dataset_root=dataset_root,
        val_ratio=0.5,
        test_ratio=0.0,
        seed=42,
    )

    assert not list(dataset_root.rglob("stale.*"))
    assert len(list((dataset_root / "images").rglob("*.jpg"))) == 2
    assert len(list((dataset_root / "labels").rglob("*.txt"))) == 2


def test_prepare_yolo_dataset_skips_zero_area_rects(tmp_path):
    media_root = tmp_path / "media"
    upload_dir = media_root / "upload"
    upload_dir.mkdir(parents=True)
    for name in ("mixed.jpg", "pos.jpg"):
        Image.new("RGB", (100, 80), color="white").save(upload_dir / name)

    mixed = _task_sample(
        1,
        "mixed.jpg",
        _sample_result(
            [
                {"x": 0, "y": 0, "width": 0, "height": 0, "label": "garbage"},
                {"x": 10, "y": 10, "width": 20, "height": 12, "label": "garbage"},
            ]
        ),
    )
    positive = _task_sample(
        2,
        "pos.jpg",
        _sample_result(
            [{"x": 20, "y": 16, "width": 18, "height": 10, "label": "garbage"}]
        ),
    )

    train_n, val_n, test_n, classes, _ = prepare_yolo_dataset(
        task_config=_garbage_street_config(),
        samples=[mixed, positive],
        media_root=media_root,
        dataset_root=tmp_path / "dataset",
        val_ratio=0.5,
        test_ratio=0.0,
        seed=42,
    )

    assert (train_n, val_n, test_n) == (1, 1, 0)
    assert classes == ["garbage", "non_garbage"]

    label_lines = []
    for path in (tmp_path / "dataset" / "labels").rglob("*.txt"):
        label_lines.extend(
            [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )

    assert len(label_lines) == 2
    assert all(
        " 0.000000 0.000000 0.000000 0.000000" not in line for line in label_lines
    )

    summary = json.loads(
        (tmp_path / "reports" / "export_summary.json").read_text(encoding="utf-8")
    )
    assert summary["stats"]["skipped_invalid_boxes"] == 1


def test_validate_yolo_training_prerequisites_ignores_zero_area_only_samples():
    cfg = _garbage_street_config()
    zero_only = _task_sample(
        1,
        "zero.jpg",
        _sample_result([{"x": 0, "y": 0, "width": 0, "height": 0, "label": "garbage"}]),
    )
    valid = _task_sample(
        2,
        "valid.jpg",
        _sample_result(
            [{"x": 10, "y": 10, "width": 20, "height": 12, "label": "garbage"}]
        ),
    )

    try:
        validate_yolo_training_prerequisites(cfg, [zero_only, valid])
    except ValueError as exc:
        assert "至少需要 2 个" in str(exc)
    else:
        raise AssertionError("zero-area-only sample should not count as usable")
