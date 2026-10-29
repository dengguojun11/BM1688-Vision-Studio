"""任务列表页图片文件名模糊搜索。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.attachment import TaskAttachment


@pytest.fixture
def image_search_tasks(client: TestClient, testuser_token_headers, db):
    from labelu.internal.adapter.persistence import crud_annotation_taxonomy, crud_task

    with db.begin():
        crud_annotation_taxonomy.label_preset_create(
            db,
            slug="pytest_search_road",
            name="搜索道路",
            description=None,
            sort_order=10,
            items=[
                {
                    "label_key": "灯",
                    "label_value": "light",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        )
        road_task = crud_task.create(
            db,
            Task(
                name="search-road-task",
                description="",
                tips="",
                config=json.dumps({"defaultRectPresetId": "pytest_search_road"}),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=1,
            ),
        )
        other_task = crud_task.create(
            db,
            Task(
                name="search-other-task",
                description="",
                tips="",
                config=json.dumps({"defaultRectPresetId": "pytest_garbage_x"}),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=1,
            ),
        )
        for task, fname in (
            (road_task, "highway_segment_001.jpg"),
            (road_task, "street_view.png"),
            (other_task, "highway_other.jpg"),
        ):
            att = TaskAttachment(
                filename=fname,
                url=f"/media/{fname}",
                path=f"/tmp/{fname}",
                task_id=task.id,
                created_by=1,
                updated_by=1,
            )
            db.add(att)
            db.flush()
            db.add(
                TaskSample(
                    inner_id=1,
                    task_id=task.id,
                    file_id=att.id,
                    created_by=1,
                    updated_by=1,
                    state=SampleState.NEW.value,
                    data="{}",
                )
            )
    yield {
        "road_task_id": road_task.id,
        "other_task_id": other_task.id,
        "road_slug": "pytest_search_road",
    }


def test_image_search_all_tasks(
    client: TestClient, testuser_token_headers, image_search_tasks
):
    r = client.get(
        "/api/v1/tasks/image-search",
        params={"q": "highway", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta_data"]["total"] == 2
    filenames = {row["filename"] for row in body["data"]}
    assert "highway_segment_001.jpg" in filenames
    assert "highway_other.jpg" in filenames


def test_image_search_scoped_by_rect_preset(
    client: TestClient, testuser_token_headers, image_search_tasks
):
    r = client.get(
        "/api/v1/tasks/image-search",
        params={
            "q": "highway",
            "page": 0,
            "size": 20,
            "rect_preset_slug": image_search_tasks["road_slug"],
        },
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta_data"]["total"] == 1
    assert body["data"][0]["filename"] == "highway_segment_001.jpg"
    assert body["data"][0]["task_id"] == image_search_tasks["road_task_id"]


def test_image_search_partial_filename(
    client: TestClient, testuser_token_headers, image_search_tasks
):
    r = client.get(
        "/api/v1/tasks/image-search",
        params={"q": "segment", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["meta_data"]["total"] == 1
    assert r.json()["data"][0]["filename"] == "highway_segment_001.jpg"


def test_image_search_scoped_by_global_label(
    client: TestClient,
    testuser_token_headers,
    testadmin_token_headers,
    db,
):
    label_response = client.post(
        "/api/v1/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        json={
            "label_key": "图片搜索标签",
            "label_value": "image_search_contract_label",
            "annotation_kind": "bbox",
            "color": "#1677ff",
        },
    )
    assert label_response.status_code == 201, label_response.text
    label_id = label_response.json()["data"]["id"]

    created = client.post(
        "/api/v1/tasks",
        headers=testuser_token_headers,
        json={"name": "label image search", "media_type": "IMAGE"},
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["data"]["id"]
    applied = client.post(
        f"/api/v1/tasks/{task_id}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={"label_ids": [label_id]},
    )
    assert applied.status_code == 200, applied.text

    with db.begin():
        attachment = TaskAttachment(
            filename="contract_scoped_target.jpg",
            url="/media/contract_scoped_target.jpg",
            path="/tmp/contract_scoped_target.jpg",
            task_id=task_id,
            created_by=1,
            updated_by=1,
        )
        db.add(attachment)
        db.flush()
        db.add(
            TaskSample(
                inner_id=1,
                task_id=task_id,
                file_id=attachment.id,
                created_by=1,
                updated_by=1,
                state=SampleState.NEW.value,
                data="{}",
            )
        )

    response = client.get(
        "/api/v1/tasks/image-search",
        params={"q": "contract_scoped", "label_ids": label_id},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["meta_data"]["total"] == 1
    assert response.json()["data"][0]["task_id"] == task_id
