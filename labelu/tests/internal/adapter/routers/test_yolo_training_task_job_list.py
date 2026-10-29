from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.tests.internal.application.service.test_training_task import (
    _get_test_user,
    _seed_training_preview_fixture,
)


def test_global_train_job_list_includes_training_task_fields(
    client: TestClient,
    testuser_token_headers,
    db,
    monkeypatch,
):
    _seed_training_preview_fixture(db)
    training_task_service = importlib.import_module(
        "labelu.internal.application.service.training_task"
    )
    cmd_mod = importlib.import_module("labelu.internal.application.command.training_task")
    monkeypatch.setattr(
        training_task_service,
        "_enqueue_label_dimension_train_job",
        lambda *args, **kwargs: None,
    )

    user = _get_test_user(db)
    created = training_task_service.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="零散垃圾训练任务",
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

    response = client.get(
        f"{settings.API_V1_STR}/yolo-train-jobs?page=0&size=20",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    row = next(item for item in rows if item["id"] == created.latest_job_id)
    assert row["training_task_id"] == created.training_task_id
    assert row["training_task_name"] == "零散垃圾训练任务"
    assert row["training_task_mode"] == "label_dimension"
    assert row["training_task_display_subtitle"]


def test_train_job_detail_includes_training_task_detail_snapshot(
    client: TestClient,
    testuser_token_headers,
    db,
    monkeypatch,
):
    _seed_training_preview_fixture(db)
    training_task_service = importlib.import_module(
        "labelu.internal.application.service.training_task"
    )
    cmd_mod = importlib.import_module("labelu.internal.application.command.training_task")
    monkeypatch.setattr(
        training_task_service,
        "_enqueue_label_dimension_train_job",
        lambda *args, **kwargs: None,
    )

    user = _get_test_user(db)
    created = training_task_service.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=cmd_mod.CreateLabelDimensionTrainingTaskCommand(
            name="零散垃圾训练任务",
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

    response = client.get(
        f"{settings.API_V1_STR}/tasks/{created.primary_task_id}/yolo-train-jobs/{created.latest_job_id}",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["training_task_id"] == created.training_task_id
    assert data["training_task_detail"]["name"] == "零散垃圾训练任务"
    assert data["training_task_detail"]["mode"] == "label_dimension"
    assert data["training_task_detail"]["selected_label_items"][0]["display_name"]
    assert data["training_task_detail"]["source_tasks"]
    assert "ignored_tasks" in data["training_task_detail"]

    global_response = client.get(
        f"{settings.API_V1_STR}/yolo-train-jobs/{created.latest_job_id}",
        headers=testuser_token_headers,
    )
    assert global_response.status_code == 200, global_response.text
    global_data = global_response.json()["data"]
    assert global_data["id"] == created.latest_job_id
    assert global_data["training_task_detail"]["name"] == "零散垃圾训练任务"
