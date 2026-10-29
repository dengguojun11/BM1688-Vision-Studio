from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, TaskStatus


@pytest.fixture
def test_user_id(db):
    from labelu.internal.adapter.persistence import crud_user

    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    user_id = int(user.id)
    db.rollback()
    db.expunge_all()
    return user_id


@pytest.fixture
def eligible_training_tasks(db, test_user_id):
    from labelu.internal.adapter.persistence import (
        crud_annotation_taxonomy,
        crud_sample,
        crud_task,
        crud_user,
    )
    from labelu.internal.application.service import annotation_contract
    from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
    from labelu.internal.domain.models.sample import SampleState, TaskSample
    from labelu.internal.domain.models.task import Task

    with db.begin():
        admin = crud_user.get_user_by_username(db, username="admin@123.com")
        assert admin is not None
        system_label = AnnotationLabel(
            label_key="路灯",
            label_value="street_light",
            normalized_label_value="street_light",
            annotation_kind="bbox",
            color="#aabbcc",
            attributes_json="[]",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(system_label)
        db.flush()
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
            name="垃圾暴露",
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
        explicit_task = crud_task.create(
            db,
            Task(
                name="pytest-road-explicit",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_road"}, ensure_ascii=False
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=test_user_id,
                training_ready=True,
            ),
        )
        fallback_task = crud_task.create(
            db,
            Task(
                name="pytest-road-fallback",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {"key": "路灯", "value": "street_light"},
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=test_user_id,
                training_ready=True,
            ),
        )
        fallback_unknown_preset_task = crud_task.create(
            db,
            Task(
                name="pytest-road-fallback-unknown-preset",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "defaultRectPresetId": "legacy_unknown_slug",
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {"key": "璺伅", "value": "street_light"},
                                    ]
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=test_user_id,
                training_ready=True,
            ),
        )
        not_ready_task = crud_task.create(
            db,
            Task(
                name="pytest-road-not-ready",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_road"}, ensure_ascii=False
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=test_user_id,
                training_ready=False,
            ),
        )
        empty_consistent_pool_task = crud_task.create(
            db,
            Task(
                name="pytest-road-empty-consistent-pool",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "defaultRectPresetId": "pytest_road",
                        "consistent_pool": {
                            "kind": "train_filtered_consistent",
                            "rect_preset_slug": "pytest_road",
                        },
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=admin.id,
                source_type="consistent_pool",
                source_system="labelu",
                source_external_task_id="pytest-road-empty-signature",
                training_ready=False,
            ),
        )
        inprogress_ready_task = crud_task.create(
            db,
            Task(
                name="pytest-road-inprogress-ready",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_road"}, ensure_ascii=False
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=test_user_id,
                training_ready=True,
            ),
        )
        configured_not_ready_task = crud_task.create(
            db,
            Task(
                name="pytest-road-configured-not-ready",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_road"}, ensure_ascii=False
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.CONFIGURED.value,
                created_by=test_user_id,
                training_ready=False,
            ),
        )
        draft_task = crud_task.create(
            db,
            Task(
                name="pytest-road-draft",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_road"}, ensure_ascii=False
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.DRAFT.value,
                created_by=test_user_id,
                training_ready=False,
            ),
        )
        other_preset_task = crud_task.create(
            db,
            Task(
                name="pytest-garbage-explicit",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_garbage"}, ensure_ascii=False
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=test_user_id,
                training_ready=True,
            ),
        )
        consistent_pool_task = crud_task.create(
            db,
            Task(
                name="pytest-road-consistent-pool",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "defaultRectPresetId": "pytest_road",
                        "consistent_pool": {
                            "kind": "train_filtered_consistent",
                            "rect_preset_slug": "pytest_road",
                        },
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=admin.id,
                source_type="consistent_pool",
                source_system="labelu",
                source_external_task_id="pytest-road-consistent-signature",
                training_ready=True,
            ),
        )
        pool_contract = annotation_contract.create_contract(
            db,
            task=consistent_pool_task,
            label_ids=[system_label.id],
            created_by=admin.id,
        )
        pool_sample_data = json.dumps(
            {
                "result": json.dumps(
                    {
                        "rectTool": {
                            "result": [
                                {
                                    "label": "street_light",
                                    "x": 1,
                                    "y": 1,
                                    "width": 10,
                                    "height": 10,
                                },
                            ]
                        }
                    }
                )
            }
        )
        crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=consistent_pool_task.id,
                    file_id=1,
                    created_by=admin.id,
                    updated_by=admin.id,
                    state=SampleState.DONE.value,
                    data=pool_sample_data,
                )
            ],
        )
        pool_sample = (
            db.query(TaskSample)
            .filter(TaskSample.task_id == consistent_pool_task.id)
            .one()
        )
        annotation_contract.upsert_sample_scope(
            db,
            sample=pool_sample,
            contract=pool_contract,
            annotation_data=json.loads(pool_sample_data),
            state=SampleState.DONE.value,
            reviewed_by=admin.id,
            explicit_negative=False,
        )
        video_task = crud_task.create(
            db,
            Task(
                name="pytest-road-video",
                description="",
                tips="",
                config=json.dumps(
                    {"defaultRectPresetId": "pytest_road"}, ensure_ascii=False
                ),
                media_type=MediaType.VIDEO.value,
                status=TaskStatus.FINISHED.value,
                created_by=test_user_id,
                training_ready=True,
            ),
        )
        ids = {
            "explicit": explicit_task.id,
            "fallback": fallback_task.id,
            "fallback_unknown_preset": fallback_unknown_preset_task.id,
            "not_ready": not_ready_task.id,
            "inprogress_ready": inprogress_ready_task.id,
            "configured_not_ready": configured_not_ready_task.id,
            "draft": draft_task.id,
            "other_preset": other_preset_task.id,
            "consistent_pool": consistent_pool_task.id,
            "empty_consistent_pool": empty_consistent_pool_task.id,
            "video": video_task.id,
        }
    yield ids


def test_yolo_train_eligible_tasks_requires_labels_or_legacy_preset(
    client: TestClient, testuser_token_headers
):
    response = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks?page=0&size=20",
        headers=testuser_token_headers,
    )
    assert response.status_code == 400, response.text
    assert "label_ids" in response.text


def test_yolo_train_eligible_tasks_uses_current_label_contract(
    client: TestClient,
    testuser_token_headers,
    testadmin_token_headers,
    db,
):
    from labelu.internal.application.service import annotation_contract
    from labelu.internal.common.db import begin_transaction
    from labelu.internal.domain.models.sample import TaskSample
    from labelu.internal.domain.models.task import Task

    label_response = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        json={
            "label_key": "候选标签",
            "label_value": "candidate_contract_label",
            "annotation_kind": "bbox",
            "color": "#1677ff",
        },
    )
    assert label_response.status_code == 201, label_response.text
    label_id = label_response.json()["data"]["id"]

    task_response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "contract candidate", "media_type": "IMAGE"},
    )
    assert task_response.status_code == 201, task_response.text
    task_id = task_response.json()["data"]["id"]
    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={"label_ids": [label_id]},
    )
    assert applied.status_code == 200, applied.text

    category_response = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/dataset-categories",
        headers=testadmin_token_headers,
        json={
            "category_key": "candidate_contract_category",
            "name": "候选契约分类",
        },
    )
    assert category_response.status_code == 201, category_response.text
    category_id = category_response.json()["data"]["id"]
    assigned = client.patch(
        f"{settings.API_V1_STR}/tasks/batch-dataset-category",
        headers=testuser_token_headers,
        json={"task_ids": [task_id], "dataset_category_id": category_id},
    )
    assert assigned.status_code == 200, assigned.text

    with begin_transaction(db):
        task = db.query(Task).filter(Task.id == task_id).one()
        contract = annotation_contract.current_contract(db, task_id)
        sample = TaskSample(
            task_id=task_id,
            inner_id=1,
            created_by=task.created_by,
            state="DONE",
            data=json.dumps(
                {
                    "result": json.dumps(
                        {
                            "width": 100,
                            "height": 100,
                            "rectTool": {
                                "result": [
                                    {
                                        "label": "candidate_contract_label",
                                        "x": 1,
                                        "y": 1,
                                        "width": 10,
                                        "height": 10,
                                    }
                                ]
                            }
                        }
                    )
                }
            ),
        )
        db.add(sample)
        db.flush()
        # Historical tasks can receive their label contract after annotation,
        # so no SampleAnnotationScope exists for this completed sample.
        contract.status = "active"
        db.add(contract)
        # A partially complete task remains selectable when its current
        # contract still has usable completed samples.
        task.training_ready = False
        db.add(task)

    response = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params=[("label_ids", label_id), ("page", 0), ("size", 20)],
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [row["task_id"] for row in rows] == [task_id]
    assert rows[0]["match_source"] == "label_contract"
    assert rows[0]["resolved_rect_preset_slug"] == ""
    assert rows[0]["training_ready"] is True
    assert rows[0]["can_train"] is False
    assert "NO_TRAINABLE_SAMPLES" in rows[0]["training_block_reasons"]

    task_first_response = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params={"task_dimension": "true", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert task_first_response.status_code == 200, task_first_response.text
    task_first_rows = task_first_response.json()["data"]
    task_first_row = next(row for row in task_first_rows if row["task_id"] == task_id)
    assert task_first_row["match_source"] == "task_contract"
    assert task_first_row["training_ready"] is True
    assert task_first_row["done_count"] == 1
    assert task_first_row["label_ids"] == [label_id]
    assert (
        task_first_row["contract_labels"][0]["label_value"]
        == "candidate_contract_label"
    )

    filtered_response = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params=[
            ("task_dimension", "true"),
            ("dataset_category_ids", category_id),
            ("label_ids", label_id),
            ("page", 0),
            ("size", 20),
        ],
        headers=testuser_token_headers,
    )
    assert filtered_response.status_code == 200, filtered_response.text
    assert [row["task_id"] for row in filtered_response.json()["data"]] == [task_id]

    wrong_category_response = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params={
            "task_dimension": "true",
            "dataset_category_ids": category_id + 1,
            "label_ids": label_id,
            "page": 0,
            "size": 20,
        },
        headers=testuser_token_headers,
    )
    assert wrong_category_response.status_code == 200
    assert wrong_category_response.json()["data"] == []

    with begin_transaction(db):
        contract = annotation_contract.current_contract(db, task_id)
        contract.status = "review_required"
        db.add(contract)

    for params in (
        [("label_ids", label_id), ("page", 0), ("size", 20)],
        {"task_dimension": "true", "page": 0, "size": 20},
    ):
        pending_review_response = client.get(
            f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
            params=params,
            headers=testuser_token_headers,
        )
        assert pending_review_response.status_code == 200
        pending_rows = pending_review_response.json()["data"]
        assert any(row["task_id"] == task_id for row in pending_rows)


def test_yolo_train_eligible_tasks_returns_finished_candidates_and_consistent_pool(
    client: TestClient,
    testuser_token_headers,
    testadmin_token_headers,
    eligible_training_tasks,
):
    response = client.get(
        (
            f"{settings.API_V1_STR}/yolo-train/eligible-tasks"
            "?rect_preset_slug=pytest_road&page=0&size=20"
        ),
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = payload["data"]
    assert payload["meta_data"]["total"] == 3

    ids = {row["task_id"] for row in rows}
    assert ids == {
        eligible_training_tasks["explicit"],
        eligible_training_tasks["fallback"],
        eligible_training_tasks["fallback_unknown_preset"],
    }

    rows_by_id = {row["task_id"]: row for row in rows}
    assert (
        rows_by_id[eligible_training_tasks["explicit"]]["match_source"]
        == "explicit_preset"
    )
    assert (
        rows_by_id[eligible_training_tasks["fallback"]]["match_source"]
        == "class_fallback"
    )
    assert (
        rows_by_id[eligible_training_tasks["fallback_unknown_preset"]]["match_source"]
        == "class_fallback"
    )
    assert (
        rows_by_id[eligible_training_tasks["explicit"]]["resolved_rect_preset_slug"]
        == "pytest_road"
    )
    assert rows_by_id[eligible_training_tasks["explicit"]]["training_ready"] is True
    assert eligible_training_tasks["consistent_pool"] not in rows_by_id
    assert all(row["task_id"] != eligible_training_tasks["not_ready"] for row in rows)
    assert all(
        row["task_id"] != eligible_training_tasks["configured_not_ready"]
        for row in rows
    )
    assert all(
        row["task_id"] != eligible_training_tasks["inprogress_ready"] for row in rows
    )
    assert all(row["task_id"] != eligible_training_tasks["draft"] for row in rows)
    assert all(
        row["task_id"] != eligible_training_tasks["other_preset"] for row in rows
    )
    assert all(row["task_id"] != eligible_training_tasks["video"] for row in rows)

    admin_response = client.get(
        (
            f"{settings.API_V1_STR}/yolo-train/eligible-tasks"
            "?rect_preset_slug=pytest_road&page=0&size=20"
        ),
        headers=testadmin_token_headers,
    )
    assert admin_response.status_code == 200, admin_response.text
    admin_rows = {row["task_id"]: row for row in admin_response.json()["data"]}
    assert (
        admin_rows[eligible_training_tasks["consistent_pool"]]["match_source"]
        == "consistent_pool"
    )
    assert admin_rows[eligible_training_tasks["consistent_pool"]]["done_count"] == 1
    assert eligible_training_tasks["empty_consistent_pool"] not in admin_rows

    admin_task_dimension_response = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params={"task_dimension": "true", "page": 0, "size": 100},
        headers=testadmin_token_headers,
    )
    assert admin_task_dimension_response.status_code == 200
    admin_task_dimension_ids = {
        row["task_id"] for row in admin_task_dimension_response.json()["data"]
    }
    assert eligible_training_tasks["consistent_pool"] in admin_task_dimension_ids


def test_yolo_train_eligible_tasks_rejects_unknown_rect_preset_slug(
    client: TestClient, testuser_token_headers
):
    response = client.get(
        (
            f"{settings.API_V1_STR}/yolo-train/eligible-tasks"
            "?rect_preset_slug=not_exists&page=0&size=20"
        ),
        headers=testuser_token_headers,
    )
    assert response.status_code == 400, response.text
    assert "rect_preset_slug" in response.text


def test_yolo_train_task_dimension_candidates_are_project_scoped(
    client: TestClient,
    testadmin_token_headers,
    eligible_training_tasks,
    db,
):
    """The create-training candidate list must not leak another project."""
    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.common.db import begin_transaction
    from labelu.internal.domain.models.project import Project
    from labelu.internal.domain.models.task import Task

    admin = crud_user.get_user_by_username(db, username="admin@123.com")
    assert admin is not None
    with begin_transaction(db):
        project_a = Project(
            owner_id=admin.id,
            name="candidate-scope-a",
            status="ACTIVE",
            created_by=admin.id,
            updated_by=admin.id,
        )
        project_b = Project(
            owner_id=admin.id,
            name="candidate-scope-b",
            status="ACTIVE",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add_all([project_a, project_b])
        db.flush()
        pool = db.query(Task).filter(Task.id == eligible_training_tasks["consistent_pool"]).one()
        pool.project_id = project_a.id
        db.add(pool)

    scoped = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params={"task_dimension": "true", "project_id": project_a.id, "page": 0, "size": 100},
        headers=testadmin_token_headers,
    )
    assert scoped.status_code == 200, scoped.text
    assert eligible_training_tasks["consistent_pool"] in {row["task_id"] for row in scoped.json()["data"]}

    other = client.get(
        f"{settings.API_V1_STR}/yolo-train/eligible-tasks",
        params={"task_dimension": "true", "project_id": project_b.id, "page": 0, "size": 100},
        headers=testadmin_token_headers,
    )
    assert other.status_code == 200, other.text
    assert eligible_training_tasks["consistent_pool"] not in {row["task_id"] for row in other.json()["data"]}
