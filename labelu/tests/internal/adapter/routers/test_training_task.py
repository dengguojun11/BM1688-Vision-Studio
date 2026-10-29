from __future__ import annotations

import asyncio
import importlib
import threading

from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.tests.internal.application.service.test_training_task import (
    _get_test_user,
    _seed_training_preview_fixture,
)


def test_training_task_create_offloads_blocking_service(monkeypatch):
    router = importlib.import_module("labelu.internal.adapter.routers.yolo_train")
    service_thread_id = None
    service_kwargs = {}

    def fake_create(*args, **kwargs):
        nonlocal service_thread_id
        service_kwargs.update(kwargs)
        service_thread_id = threading.get_ident()
        return router.TrainingTaskCreateResponse(
            training_task_id=63,
            name="training-task",
            mode="task_dimension",
            primary_task_id=20,
            latest_job_id=312,
            included_task_count=1,
            included_image_count=4214,
            ignored_task_count=0,
            message="created",
        )

    monkeypatch.setattr(
        router.training_task_service,
        "create_label_dimension_training_task",
        fake_create,
    )

    async def invoke():
        event_loop_thread_id = threading.get_ident()
        response = await router.create_training_task(
            cmd=router.CreateLabelDimensionTrainingTaskCommand(name="training-task"),
            idempotency_key="ui-training-retry",
            authorization=None,
            db=object(),
            current_user=object(),
        )
        return event_loop_thread_id, response

    event_loop_thread_id, response = asyncio.run(invoke())

    assert response.data.latest_job_id == 312
    assert service_thread_id is not None
    assert service_thread_id != event_loop_thread_id
    assert service_kwargs["idempotency_key"] == "ui-training-retry"


def test_training_task_name_check_route(client: TestClient, testuser_token_headers):
    response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks/name-check",
        headers=testuser_token_headers,
        json={"name": "零散垃圾训练任务"},
    )
    assert response.status_code == 200, response.text
    assert "available" in response.json()["data"]


def test_training_task_list_accepts_canonical_q_search(
    client: TestClient, testuser_token_headers, monkeypatch
):
    router = importlib.import_module("labelu.internal.adapter.routers.yolo_train")
    captured = {}

    def fake_list(*args, **kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(router.training_task_service, "list_training_tasks", fake_list)

    response = client.get(
        f"{settings.API_V1_STR}/yolo-training-tasks",
        headers=testuser_token_headers,
        params={"q": "task%_name", "page": 2, "size": 7},
    )

    assert response.status_code == 200, response.text
    assert captured["search"] == "task%_name"
    assert captured["page"] == 2
    assert captured["size"] == 7
    assert response.json()["meta_data"] == {"total": 0, "page": 2, "size": 0}


def test_training_task_name_check_rejects_encoding_replacement_markers(
    client: TestClient, testuser_token_headers
):
    response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks/name-check",
        headers=testuser_token_headers,
        json={"name": "????-全量训练-20260808"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["msg"] == "训练任务名称疑似乱码，请使用 UTF-8 编码重新提交"


def test_training_task_create_rejects_encoding_replacement_markers(
    client: TestClient, testuser_token_headers
):
    response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks",
        headers=testuser_token_headers,
        json={"name": "鸟类\ufffd别-全量训练-20260808"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["msg"] == "训练任务名称疑似乱码，请使用 UTF-8 编码重新提交"


def test_training_task_preview_route(client: TestClient, testuser_token_headers, db):
    _seed_training_preview_fixture(db)
    response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks/preview",
        headers=testuser_token_headers,
        json={
            "selected_presets": [{"preset_slug": "preset_a"}, {"preset_slug": "preset_b"}],
            "selected_label_items": [
                {"preset_slug": "preset_a", "label_value": "scatter_a"},
                {"preset_slug": "preset_b", "label_value": "scatter_b"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["merged_class_count"] == 2


def test_training_task_create_and_detail_routes(client: TestClient, testuser_token_headers, db, monkeypatch):
    _seed_training_preview_fixture(db)
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    monkeypatch.setattr(svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None)

    create_response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks",
        headers=testuser_token_headers,
        json={
            "name": "零散垃圾训练任务",
            "selected_presets": [{"preset_slug": "preset_a"}, {"preset_slug": "preset_b"}],
            "selected_label_items": [
                {"preset_slug": "preset_a", "label_value": "scatter_a"},
                {"preset_slug": "preset_b", "label_value": "scatter_b"},
            ],
            "model_weights": "yolo11n.pt",
            "weights_source": "preset",
            "epochs": 50,
            "imgsz": 640,
            "hyperparams": {},
        },
    )
    assert create_response.status_code == 200, create_response.text
    training_task_id = create_response.json()["data"]["training_task_id"]

    detail_response = client.get(
        f"{settings.API_V1_STR}/yolo-training-tasks/{training_task_id}",
        headers=testuser_token_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["data"]["name"] == "零散垃圾训练任务"
    assert detail_response.json()["data"]["selected_label_items"][0]["preset_slug"] == "preset_a"
    assert detail_response.json()["data"]["selected_label_items"][0]["label_value"] == "scatter_a"


def test_training_task_iterate_route(client: TestClient, testuser_token_headers, db, monkeypatch):
    _seed_training_preview_fixture(db)
    svc = importlib.import_module("labelu.internal.application.service.training_task")
    monkeypatch.setattr(svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None)
    create_response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks",
        headers=testuser_token_headers,
        json={
            "name": "零散垃圾训练任务",
            "selected_presets": [{"preset_slug": "preset_a"}],
            "selected_label_items": [
                {"preset_slug": "preset_a", "label_value": "scatter_a"},
            ],
            "model_weights": "yolo11n.pt",
            "weights_source": "preset",
            "epochs": 50,
            "imgsz": 640,
            "hyperparams": {},
        },
    )
    training_task_id = create_response.json()["data"]["training_task_id"]
    iterate_response = client.post(
        f"{settings.API_V1_STR}/yolo-training-tasks/{training_task_id}/iterate",
        headers=testuser_token_headers,
        json={},
    )
    assert iterate_response.status_code == 200, iterate_response.text
    assert iterate_response.json()["data"]["name"] == "零散垃圾训练任务-v2"
