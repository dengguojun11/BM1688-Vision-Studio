from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.application.response.yolo_preannotate import (
    YoloPreannotateJobResponse,
    YoloPreannotateModelsResponse,
)


def test_yolo_preannotate_single_sample_query_overrides_stale_body(
    client: TestClient, testuser_token_headers: dict, monkeypatch
) -> None:
    captured = {}

    async def fake_create_batch_job(db, task_id, cmd, current_user):
        captured["task_id"] = task_id
        captured["sample_ids"] = cmd.sample_ids
        captured["sample_id"] = cmd.sample_id
        captured["apply_to_canvas_overwrite"] = cmd.apply_to_canvas_overwrite
        captured["agnostic_nms"] = cmd.agnostic_nms
        return YoloPreannotateJobResponse(
            id=1,
            task_id=task_id,
            status="PROCESSING",
            sample_count=1,
            processed_count=0,
            success_count=0,
            failed_count=0,
            model_kind=cmd.model_kind,
            model_id=cmd.model_id,
        )

    monkeypatch.setattr(
        "labelu.internal.adapter.routers.sample.yolo_preannotate_service.create_batch_job",
        fake_create_batch_job,
    )

    r = client.post(
        f"{settings.API_V1_STR}/tasks/95/yolo_preannotate_job",
        params={"sample_id": 103411},
        headers=testuser_token_headers,
        json={
            "model_kind": "trained",
            "model_id": 219,
            "sample_ids": [102339],
            "sample_id": 102339,
            "apply_to_canvas_overwrite": False,
            "agnostic_nms": True,
        },
    )

    assert r.status_code == 200
    assert captured == {
        "task_id": 95,
        "sample_ids": [103411],
        "sample_id": 103411,
        "apply_to_canvas_overwrite": False,
        "agnostic_nms": True,
    }


def test_yolo_preannotate_model_candidates_forward_search_and_page(
    client: TestClient, testuser_token_headers: dict, monkeypatch
) -> None:
    captured = {}

    def fake_list_models_for_task(db, task_id, current_user, **kwargs):
        captured.update({"task_id": task_id, **kwargs})
        return YoloPreannotateModelsResponse(
            items=[],
            task_labels=[],
            task_preset_slug="",
            total=0,
            page=kwargs["page"],
            size=0,
        )

    monkeypatch.setattr(
        "labelu.internal.adapter.routers.sample.yolo_preannotate_service.list_models_for_task",
        fake_list_models_for_task,
    )

    response = client.get(
        f"{settings.API_V1_STR}/tasks/95/yolo_preannotate/models",
        params={"q": "needle", "page": 2, "size": 7},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert captured == {"task_id": 95, "search": "needle", "page": 2, "size": 7}


def test_yolo_preannotate_jobs_forward_search_and_return_meta(
    client: TestClient, testuser_token_headers: dict, monkeypatch
) -> None:
    captured = {}
    job = YoloPreannotateJobResponse(
        id=1,
        task_id=95,
        status="FAILED",
        sample_count=1,
        processed_count=1,
        success_count=0,
        failed_count=1,
        model_kind="imported",
        model_id=7,
    )

    def fake_list_jobs_for_task(db, task_id, current_user, **kwargs):
        captured.update({"task_id": task_id, **kwargs})
        return [job], 1

    monkeypatch.setattr(
        "labelu.internal.adapter.routers.sample.yolo_preannotate_service.list_jobs_for_task",
        fake_list_jobs_for_task,
    )

    response = client.get(
        f"{settings.API_V1_STR}/tasks/95/yolo-preannotate-jobs",
        params={"q": "failed", "page": 1, "size": 3},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["meta_data"] == {"total": 1, "page": 1, "size": 1}
    assert captured == {"task_id": 95, "search": "failed", "page": 1, "size": 3}
