from __future__ import annotations

import importlib
import json

import pytest

from labelu.tests.internal.application.service.test_training_task import (
    _seed_training_preview_fixture,
)


def test_build_label_dimension_dataset_context_cleans_unselected_boxes_and_merges_labels(
    db, monkeypatch
):
    user, _task_a, _task_b, _task_c = _seed_training_preview_fixture(db)
    try:
        svc = importlib.import_module("labelu.internal.application.service.training_task")
        cmd_mod = importlib.import_module("labelu.internal.application.command.training_task")
    except ModuleNotFoundError as exc:
        pytest.fail(f"training_task modules missing: {exc}")

    monkeypatch.setattr(svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None)
    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="Dataset context validation",
            selected_presets=[{"preset_slug": "preset_a"}, {"preset_slug": "preset_b"}],
            selected_label_items=[
                {"preset_slug": "preset_a", "label_value": "scatter_a"},
                {"preset_slug": "preset_b", "label_value": "scatter_b"},
            ],
            model_weights="yolo11n.pt",
            weights_source="preset",
            epochs=50,
            imgsz=640,
            hyperparams={},
        ),
    )

    task_config, samples, per_task_counts, source_task_ids = svc.build_label_dimension_dataset_context(
        db,
        current_user=user,
        training_task_id=created.training_task_id,
    )

    assert source_task_ids == sorted(source_task_ids)
    assert len(source_task_ids) == 2
    assert set(per_task_counts.keys()) == set(source_task_ids)
    rect_attrs = task_config["tools"][0]["config"]["attributes"]
    assert len(rect_attrs) == 2
    assert {item["value"] for item in rect_attrs} == {"scatter_a", "scatter_b"}
    assert all(item["key"] == "零散垃圾" for item in rect_attrs)
    assert len(samples) == 2

    parsed_labels = []
    for sample in samples:
        outer = json.loads(sample.data)
        result = json.loads(outer["result"])
        rects = result["rectTool"]["result"]
        assert len(rects) == 1
        parsed_labels.append(rects[0]["label"])

    assert set(parsed_labels) == {"scatter_a", "scatter_b"}
