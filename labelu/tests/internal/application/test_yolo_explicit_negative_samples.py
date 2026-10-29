from __future__ import annotations

import json

import pytest
from PIL import Image

from labelu.internal.application.service.sample import (
    _validate_explicit_negative_sample_data,
)
from labelu.internal.application.service.training_task import (
    _clone_sample_with_cleaned_rects,
)
from labelu.internal.application.service.yolo_dataset import (
    prepare_yolo_dataset,
    validate_merge_tasks_sample_labels,
    validate_yolo_training_prerequisites,
)
from labelu.internal.common.task_annotation_completeness import audit_one_sample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample


def _config() -> dict:
    return {
        "defaultRectPresetId": "genericDetection",
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "target", "value": "target"}]},
            }
        ],
    }


def _sample(
    sample_id: int, filename: str, rects: list[dict], *, negative: bool = False
) -> TaskSample:
    attachment = TaskAttachment()
    attachment.id = sample_id
    attachment.filename = filename
    attachment.path = f"/upload/{filename}"

    result = {
        "width": 100,
        "height": 80,
        "rectTool": {"toolName": "rectTool", "result": rects},
    }
    outer = {"result": json.dumps(result, ensure_ascii=False)}
    if negative:
        outer["training_sample_type"] = "negative"

    sample = TaskSample()
    sample.id = sample_id
    sample.task_id = 1
    sample.state = SampleState.DONE.value
    sample.file = attachment
    sample.data = json.dumps(outer, ensure_ascii=False)
    return sample


def _box() -> dict:
    return {"x": 10, "y": 10, "width": 20, "height": 12, "label": "target"}


def test_explicit_negative_passes_generic_training_audit() -> None:
    explicit = _sample(1, "negative.jpg", [], negative=True)
    unconfirmed = _sample(2, "unconfirmed.jpg", [])

    explicit_audit = audit_one_sample(explicit, _config())
    unconfirmed_audit = audit_one_sample(unconfirmed, _config())

    assert explicit_audit["rect_ok"] is True
    assert explicit_audit["ok"] is True
    assert unconfirmed_audit["rect_ok"] is False
    assert unconfirmed_audit["ok"] is False


def test_explicit_negative_counts_toward_training_prerequisites() -> None:
    validate_yolo_training_prerequisites(
        _config(),
        [
            _sample(1, "positive.jpg", [_box()]),
            _sample(2, "negative.jpg", [], negative=True),
        ],
    )


def test_negative_samples_cannot_replace_all_positive_samples() -> None:
    with pytest.raises(ValueError, match="负样本只能作为补充"):
        validate_yolo_training_prerequisites(
            _config(),
            [
                _sample(1, "negative-1.jpg", [], negative=True),
                _sample(2, "negative-2.jpg", [], negative=True),
            ],
        )


def test_label_dimension_context_keeps_only_explicit_empty_samples() -> None:
    explicit = _clone_sample_with_cleaned_rects(
        _sample(1, "negative.jpg", [], negative=True),
        {"target": "target"},
    )
    unconfirmed = _clone_sample_with_cleaned_rects(
        _sample(2, "unconfirmed.jpg", []),
        {"target": "target"},
    )

    assert explicit is not None
    explicit_outer = json.loads(explicit.data)
    assert explicit_outer["training_sample_type"] == "negative"
    assert json.loads(explicit_outer["result"])["rectTool"]["result"] == []
    assert unconfirmed is None


def test_merge_validation_accepts_explicit_negative_only_source() -> None:
    validate_merge_tasks_sample_labels(
        ["target"],
        per_task_samples={2: [_sample(2, "negative.jpg", [], negative=True)]},
        primary_task_id=1,
        task_config=_config(),
    )


def test_prepare_dataset_exports_blank_labels_and_split_composition(tmp_path) -> None:
    media_root = tmp_path / "media"
    upload_dir = media_root / "upload"
    upload_dir.mkdir(parents=True)

    samples = []
    for sample_id in range(1, 7):
        filename = f"positive-{sample_id}.jpg"
        Image.new("RGB", (100, 80), color="white").save(upload_dir / filename)
        samples.append(_sample(sample_id, filename, [_box()]))
    for sample_id in range(7, 10):
        filename = f"negative-{sample_id}.jpg"
        Image.new("RGB", (100, 80), color="white").save(upload_dir / filename)
        samples.append(_sample(sample_id, filename, [], negative=True))

    counts = prepare_yolo_dataset(
        task_config=_config(),
        samples=samples,
        media_root=media_root,
        dataset_root=tmp_path / "dataset",
        val_ratio=0.2,
        test_ratio=0.2,
        seed=42,
    )

    assert counts[:3] == (5, 2, 2)
    labels = list((tmp_path / "dataset" / "labels").rglob("*.txt"))
    assert len(labels) == 9
    assert sum(path.read_text(encoding="utf-8") == "" for path in labels) == 3

    summary = json.loads(
        (tmp_path / "reports" / "export_summary.json").read_text(encoding="utf-8")
    )
    composition = summary["sample_composition"]
    assert composition["positive_images"] == 6
    assert composition["negative_images"] == 3
    assert composition["negative_ratio"] == pytest.approx(1 / 3, abs=1e-6)
    assert all(
        composition["splits"][split]["negative_images"] >= 1
        for split in ("train", "val", "test")
    )


def test_prepare_dataset_selected_class_names_excludes_other_boxes(tmp_path) -> None:
    media_root = tmp_path / "media"
    upload_dir = media_root / "upload"
    upload_dir.mkdir(parents=True)
    config = {
        "defaultRectPresetId": "genericDetection",
        "tools": [{
            "tool": "rectTool",
            "config": {"attributes": [
                {"key": "target", "value": "target"},
                {"key": "other", "value": "other"},
            ]},
        }],
    }
    samples = []
    for sample_id in range(1, 4):
        filename = f"subset-{sample_id}.jpg"
        Image.new("RGB", (100, 80), color="white").save(upload_dir / filename)
        samples.append(
            _sample(
                sample_id,
                filename,
                [_box(), {"x": 30, "y": 20, "width": 10, "height": 10, "label": "other"}],
            )
        )

    counts = prepare_yolo_dataset(
        task_config=config,
        samples=samples,
        media_root=media_root,
        dataset_root=tmp_path / "subset-dataset",
        val_ratio=0.2,
        test_ratio=0.0,
        seed=42,
        selected_class_names=["target"],
    )

    assert counts[3] == ["target"]
    labels = list((tmp_path / "subset-dataset" / "labels").rglob("*.txt"))
    assert labels
    assert all(len(path.read_text(encoding="utf-8").splitlines()) == 1 for path in labels)
    assert all(path.read_text(encoding="utf-8").split()[0] == "0" for path in labels)


def test_explicit_negative_payload_rejects_rectangles() -> None:
    data = {
        "training_sample_type": "negative",
        "result": json.dumps({"rectTool": {"result": [_box()]}}),
    }
    with pytest.raises(Exception, match="负样本不能包含矩形框"):
        _validate_explicit_negative_sample_data(data, SampleState.DONE)


def test_explicit_negative_payload_must_be_done() -> None:
    data = {
        "training_sample_type": "negative",
        "result": json.dumps({"rectTool": {"result": []}}),
    }
    with pytest.raises(Exception, match="负样本必须提交为 DONE"):
        _validate_explicit_negative_sample_data(data, SampleState.NEW)
