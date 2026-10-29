"""任务列表 status / media_type 筛选。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import TaskStatus, MediaType
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel


@pytest.fixture
def image_finished_task(client: TestClient, testuser_token_headers, db):
    from labelu.internal.adapter.persistence import crud_task
    from labelu.internal.domain.models.task import Task

    with db.begin():
        t = Task(
            name="pytest-finished-image",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        crud_task.create(db, t)
        task_id = t.id
    yield task_id


def test_list_tasks_filter_finished_image(
    client: TestClient, testuser_token_headers, image_finished_task
):
    r = client.get(
        "/api/v1/tasks?page=0&size=50&status=FINISHED&media_type=IMAGE",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()["data"]]
    assert image_finished_task in ids
    for row in r.json()["data"]:
        assert row["status"] == "FINISHED"
        assert row["media_type"] == "IMAGE"


def test_list_tasks_filter_dataset_contract_and_search(
    client: TestClient, testadmin_token_headers, db
):
    label = AnnotationLabel(
        label_key="契约搜索标签",
        label_value="contract_search_label",
        normalized_label_value="contract_search_label",
        annotation_kind="bbox",
        color="#1677ff",
        attributes_json="[]",
        status="active",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(label)
    db.commit()

    dataset = client.post(
        "/api/v1/datasets",
        headers=testadmin_token_headers,
        json={"name": "契约搜索数据集", "label_ids": [label.id]},
    )
    assert dataset.status_code == 201, dataset.text
    dataset_payload = dataset.json()["data"]
    contract_id = dataset_payload["dataset_label_contract_id"]
    task = client.post(
        "/api/v1/tasks",
        headers=testadmin_token_headers,
        json={"name": "契约搜索任务", "media_type": "IMAGE", "dataset_id": dataset_payload["id"]},
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["data"]["id"]

    for params in (
        {"dataset_label_contract_id": contract_id},
        {"dataset_search": "契约搜索标签"},
        {"dataset_search": "契约搜索数据集"},
    ):
        response = client.get(
            "/api/v1/tasks", params={"page": 0, "size": 50, **params}, headers=testadmin_token_headers
        )
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["data"]] == [task_id]


@pytest.fixture
def preset_sidebar_tasks(client: TestClient, testuser_token_headers, db):
    from labelu.internal.adapter.persistence import crud_annotation_taxonomy, crud_task
    from labelu.internal.domain.models.task import Task

    with db.begin():
        crud_annotation_taxonomy.label_preset_create(
            db,
            slug="pytest_road",
            name="道路设施",
            description=None,
            sort_order=10,
            items=[
                {
                    "label_key": "路灯",
                    "label_value": "street_light",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        )
        crud_annotation_taxonomy.label_preset_create(
            db,
            slug="pytest_garbage",
            name="垃圾井盖",
            description=None,
            sort_order=20,
            items=[
                {
                    "label_key": "垃圾",
                    "label_value": "garbage",
                    "color": "#52c41a",
                    "sort_order": 0,
                }
            ],
        )
        road_task = crud_task.create(
            db,
            Task(
                name="pytest-road-task",
                description="",
                tips="",
                config=json.dumps({"defaultRectPresetId": "pytest_road"}),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=1,
            ),
        )
        legacy_road_task = crud_task.create(
            db,
            Task(
                name="pytest-legacy-road-task",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {
                                            "key": "路灯",
                                            "value": "street_light",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=1,
            ),
        )
        garbage_task = crud_task.create(
            db,
            Task(
                name="pytest-garbage-task",
                description="",
                tips="",
                config=json.dumps({"defaultRectPresetId": "pytest_garbage"}),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.CONFIGURED.value,
                created_by=1,
            ),
        )
        uncategorized_task = crud_task.create(
            db,
            Task(
                name="pytest-uncategorized-task",
                description="",
                tips="",
                config=json.dumps({"tools": []}),
                media_type=MediaType.VIDEO.value,
                status=TaskStatus.CONFIGURED.value,
                created_by=1,
            ),
        )
        task_ids = {
            "road": road_task.id,
            "legacy_road": legacy_road_task.id,
            "garbage": garbage_task.id,
            "uncategorized": uncategorized_task.id,
        }
    yield task_ids


def test_list_tasks_filter_rect_preset_slug(
    client: TestClient, testuser_token_headers, preset_sidebar_tasks
):
    r = client.get(
        f"{settings.API_V1_STR}/tasks?page=0&size=50&rect_preset_slug=pytest_road",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200, r.text
    ids = [row["id"] for row in r.json()["data"]]
    assert preset_sidebar_tasks["road"] in ids
    assert preset_sidebar_tasks["legacy_road"] in ids
    assert preset_sidebar_tasks["garbage"] not in ids
    assert preset_sidebar_tasks["uncategorized"] not in ids


def test_list_tasks_filter_uncategorized_rect_preset(
    client: TestClient, testuser_token_headers, preset_sidebar_tasks
):
    r = client.get(
        f"{settings.API_V1_STR}/tasks?page=0&size=50&rect_preset_slug=__uncategorized__",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200, r.text
    ids = [row["id"] for row in r.json()["data"]]
    assert preset_sidebar_tasks["uncategorized"] in ids
    assert preset_sidebar_tasks["road"] not in ids
    assert preset_sidebar_tasks["legacy_road"] not in ids
    assert preset_sidebar_tasks["garbage"] not in ids


def test_list_tasks_filter_training_ready(
    client: TestClient, testuser_token_headers, db
):
    from labelu.internal.adapter.persistence import crud_task
    from labelu.internal.domain.models.task import Task

    with db.begin():
        ready_task = crud_task.create(
            db,
            Task(
                name="pytest-training-ready",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=1,
                training_ready=True,
            ),
        )
        not_ready_task = crud_task.create(
            db,
            Task(
                name="pytest-training-not-ready",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=1,
                training_ready=False,
            ),
        )
        task_ids = {"ready": ready_task.id, "not_ready": not_ready_task.id}

    r_ready = client.get(
        f"{settings.API_V1_STR}/tasks?page=0&size=50&training_ready=true",
        headers=testuser_token_headers,
    )
    assert r_ready.status_code == 200, r_ready.text
    ready_ids = [row["id"] for row in r_ready.json()["data"]]
    assert task_ids["ready"] in ready_ids
    assert task_ids["not_ready"] not in ready_ids

    r_not = client.get(
        f"{settings.API_V1_STR}/tasks?page=0&size=50&training_ready=false",
        headers=testuser_token_headers,
    )
    assert r_not.status_code == 200, r_not.text
    not_ids = [row["id"] for row in r_not.json()["data"]]
    assert task_ids["not_ready"] in not_ids
    assert task_ids["ready"] not in not_ids


def test_task_rect_preset_summary_counts_visible_tasks(
    client: TestClient, testuser_token_headers, preset_sidebar_tasks
):
    r = client.get(
        f"{settings.API_V1_STR}/tasks/rect-preset-summary",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    presets = {row["slug"]: row for row in data["presets"]}
    assert data["all"] >= 3
    assert presets["pytest_road"]["name"] == "道路设施"
    assert presets["pytest_road"]["count"] == 2
    assert presets["pytest_garbage"]["count"] == 1
    assert data["uncategorized"] >= 1


def test_task_list_and_summary_use_global_label_contracts(
    client: TestClient,
    testuser_token_headers,
    testadmin_token_headers,
    db,
):
    labels = []
    for key, value in (("道路灯", "task_filter_light"), ("车辆", "task_filter_vehicle")):
        response = client.post(
            f"{settings.API_V1_STR}/annotation-taxonomy/labels",
            headers=testadmin_token_headers,
            json={
                "label_key": key,
                "label_value": value,
                "annotation_kind": "bbox",
                "color": "#1677ff" if value.endswith("light") else "#52c41a",
            },
        )
        assert response.status_code == 201, response.text
        labels.append(response.json()["data"])

    task_ids = []
    for name, label_ids in (
        ("label contract superset", [labels[0]["id"], labels[1]["id"]]),
        ("label contract other", [labels[1]["id"]]),
    ):
        created = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={"name": name, "media_type": "IMAGE"},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["data"]["id"]
        applied = client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-labels",
            headers=testuser_token_headers,
            json={"label_ids": label_ids},
        )
        assert applied.status_code == 200, applied.text
        task_ids.append(task_id)

    db.rollback()
    with db.begin():
        for index, task_id in enumerate(task_ids):
            for sample_index in range(3 if index == 0 else 1):
                attachment = TaskAttachment(
                    filename=f"label-filter-{index}-{sample_index}.jpg",
                    path=f"label-filter-{index}-{sample_index}.jpg",
                    url=f"/media/label-filter-{index}-{sample_index}.jpg",
                    task_id=task_id,
                    created_by=1,
                    updated_by=1,
                )
                db.add(attachment)
                db.flush()
                labels_in_sample = [labels[0]["label_value"], labels[1]["label_value"]]
                if index == 1:
                    labels_in_sample = [labels[1]["label_value"]]
                crud_sample.batch(
                    db,
                    [
                        TaskSample(
                            task_id=task_id,
                            file_id=attachment.id,
                            created_by=1,
                            updated_by=1,
                            data=json.dumps(
                                {
                                    "result": json.dumps(
                                        {
                                            "width": 100,
                                            "height": 100,
                                            "rectTool": {
                                                "result": [
                                                    {
                                                        "id": f"box-{index}-{sample_index}-{box_index}",
                                                        "label": value,
                                                        "x": 1,
                                                        "y": 1,
                                                        "width": 10,
                                                        "height": 10,
                                                    }
                                                    for box_index, value in enumerate(
                                                        labels_in_sample
                                                    )
                                                ]
                                            },
                                        }
                                    )
                                }
                            ),
                        )
                    ],
                )

    filtered = client.get(
        f"{settings.API_V1_STR}/tasks",
        params=[("page", 0), ("size", 50), ("label_ids", labels[0]["id"])],
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200, filtered.text
    filtered_ids = {row["id"] for row in filtered.json()["data"]}
    assert task_ids[0] in filtered_ids
    assert task_ids[1] not in filtered_ids

    summary = client.get(
        f"{settings.API_V1_STR}/tasks/label-summary",
        headers=testuser_token_headers,
    )
    assert summary.status_code == 200, summary.text
    summary_labels = summary.json()["data"]["labels"]
    summary_counts = [row["count"] for row in summary_labels]
    assert summary_counts == sorted(summary_counts, reverse=True)
    counts = {row["id"]: row["count"] for row in summary_labels}
    assert counts[labels[0]["id"]] == 3
    assert counts[labels[1]["id"]] == 4
    ordered_ids = [row["id"] for row in summary_labels]
    assert ordered_ids.index(labels[1]["id"]) < ordered_ids.index(labels[0]["id"])

    images = client.get(
        f"{settings.API_V1_STR}/tasks/label-images",
        params={"label_id": labels[0]["id"], "page": 0, "size": 2},
        headers=testuser_token_headers,
    )
    assert images.status_code == 200, images.text
    assert images.json()["meta_data"]["total"] == 3
    assert len(images.json()["data"]) == 2
    assert images.json()["data"][0]["width"] == 100
    assert images.json()["data"][0]["height"] == 100
    assert {
        item["label"]
        for image in images.json()["data"]
        for item in image["annotations"]
    } == {labels[0]["label_value"]}

    searched_images = client.get(
        f"{settings.API_V1_STR}/tasks/label-images",
        params={"label_id": labels[0]["id"], "q": "label-filter-0-1", "page": 0, "size": 24},
        headers=testuser_token_headers,
    )
    assert searched_images.status_code == 200, searched_images.text
    assert searched_images.json()["meta_data"]["total"] == 1
    assert [row["filename"] for row in searched_images.json()["data"]] == [
        "label-filter-0-1.jpg"
    ]

    next_page = client.get(
        f"{settings.API_V1_STR}/tasks/label-images",
        params={"label_id": labels[0]["id"], "page": 1, "size": 2},
        headers=testuser_token_headers,
    )
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["meta_data"]["total"] == 3
    assert len(next_page.json()["data"]) == 1
    first_page_ids = {row["sample_id"] for row in images.json()["data"]}
    second_page_ids = {row["sample_id"] for row in next_page.json()["data"]}
    assert first_page_ids.isdisjoint(second_page_ids)
