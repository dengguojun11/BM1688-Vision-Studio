import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.annotation_contract import TaskAnnotationContract
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User


def _create_user(db: Session, username: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if user:
        return user
    user = User(username=username, hashed_password=get_password_hash("User@123"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users/login", json={"username": username, "password": "User@123"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": response.json()["data"]["token"]}


def _version_payload(seed: str) -> dict:
    return {
        "manifest_rel_path": f"manifests/{seed}.json",
        "manifest_sha256": seed * 64,
        "label_set_signature": "a" * 64,
        "class_order_signature": "b" * 64,
        "sample_count": 12,
        "metadata_json": '{"source":"test"}',
    }


def _create_dataset(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    **payload,
) -> dict:
    response = client.post(
        "/api/v1/datasets",
        json={"name": name, **payload},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _seed_completed_task(
    db: Session,
    *,
    user_id: int,
    dataset_id: int | None = None,
    status: str = "FINISHED",
) -> Task:
    task = Task(
        name="占道经营标注任务",
        media_type="IMAGE",
        status=status,
        dataset_id=dataset_id,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(task)
    db.flush()
    contract = TaskAnnotationContract(
        task_id=task.id,
        revision=1,
        annotation_kind="bbox",
        label_ids_json="[1,2]",
        labels_snapshot_json=json.dumps(
            [
                {"label_id": 1, "label_key": "person", "label_value": "人员"},
                {"label_id": 2, "label_key": "stall", "label_value": "摊位"},
            ],
            ensure_ascii=False,
        ),
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        status="frozen",
        created_by=user_id,
    )
    db.add(contract)
    db.flush()
    task.current_annotation_contract_id = contract.id
    attachment = TaskAttachment(
        task_id=task.id,
        filename="occupied-vending.jpg",
        path=f"tasks/{task.id}/occupied-vending.jpg",
        url=f"/api/v1/tasks/attachment/tasks/{task.id}/occupied-vending.jpg",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(attachment)
    db.flush()
    db.add(
        TaskSample(
            task_id=task.id,
            file_id=attachment.id,
            created_by=user_id,
            updated_by=user_id,
            state=SampleState.DONE.value,
            data=json.dumps({"rect": [{"label": "摊位", "x": 0.5}]}),
        )
    )
    db.commit()
    db.refresh(task)
    return task


def test_dataset_version_lifecycle_without_project(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    dataset = _create_dataset(client, testuser_token_headers, name="城市治理数据集")
    assert "project_id" not in dataset

    version = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        json=_version_payload("1"),
        headers=testuser_token_headers,
    )
    assert version.status_code == 201, version.text
    version_payload = version.json()["data"]
    version_id = version_payload["id"]
    assert "project_id" not in version_payload
    assert version_payload["status"] == "INGESTING"
    assert version_payload["version_no"] == 1

    for target in ("VALIDATING", "READY_TO_REVIEW"):
        response = client.patch(
            f"/api/v1/dataset-versions/{version_id}",
            json={"status": target},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text

    reviewed = client.post(
        f"/api/v1/dataset-versions/{version_id}/review",
        json={"decision": "APPROVE", "comment": "清单、样本数和标签契约已确认"},
        headers=testuser_token_headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    frozen = client.post(
        f"/api/v1/dataset-versions/{version_id}/freeze",
        headers=testuser_token_headers,
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["data"]["status"] == "FROZEN"

    rejected = client.patch(
        f"/api/v1/dataset-versions/{version_id}",
        json={"sample_count": 99},
        headers=testuser_token_headers,
    )
    assert rejected.status_code == 409, rejected.text
    listed = client.get("/api/v1/datasets", headers=testuser_token_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["meta_data"]["total"] == 1
    assert listed.json()["data"][0]["version_count"] == 1


def test_dataset_versions_endpoint_is_searchable_and_paginated(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    dataset = _create_dataset(client, testuser_token_headers, name="版本分页数据集")
    for index, seed in enumerate(("first", "second"), start=1):
        response = client.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            json={
                **_version_payload(seed),
                "manifest_sha256": ("a" * 63) + str(index),
                "metadata_json": json.dumps({"source": seed}),
            },
            headers=testuser_token_headers,
        )
        assert response.status_code == 201, response.text

    response = client.get(
        f"/api/v1/datasets/{dataset['id']}/versions?page=0&size=1&q=second",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta_data"]["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["manifest_rel_path"] == "manifests/second.json"


def test_global_dataset_version_search_is_paginated_and_includes_dataset_name(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    first = _create_dataset(
        client, testuser_token_headers, name="回流停车数据集"
    )
    second = _create_dataset(
        client, testuser_token_headers, name="回流道路数据集"
    )
    for dataset, seed, source in (
        (first, "a", "active_learning_batch"),
        (second, "b", "active_learning_batch"),
        (first, "c", "manual_import"),
    ):
        response = client.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            json={
                **_version_payload(seed),
                "metadata_json": json.dumps({"source": source}),
            },
            headers=testuser_token_headers,
        )
        assert response.status_code == 201, response.text

    page = client.get(
        "/api/v1/dataset-versions?page=0&size=1&active_learning_only=true",
        headers=testuser_token_headers,
    )
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["meta_data"]["total"] == 2
    assert len(body["data"]) == 1
    assert body["data"][0]["dataset_name"] in {"回流停车数据集", "回流道路数据集"}

    searched = client.get(
        "/api/v1/dataset-versions?q=道路&active_learning_only=true",
        headers=testuser_token_headers,
    )
    assert searched.status_code == 200, searched.text
    searched_body = searched.json()
    assert searched_body["meta_data"]["total"] == 1
    assert searched_body["data"][0]["dataset_name"] == "回流道路数据集"


def test_dataset_version_generated_from_authorized_task_has_no_project_lineage(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    dataset = _create_dataset(client, testuser_token_headers, name="占道经营数据集")
    task = _seed_completed_task(db, user_id=1)

    response = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions/from-tasks",
        json={"source_task_ids": [task.id]},
        headers=testuser_token_headers,
    )
    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["manifest_rel_path"].startswith(f"datasets/dataset-{dataset['id']}/")
    assert "project" not in payload["manifest_rel_path"]
    metadata = json.loads(payload["metadata_json"])
    assert metadata["source_task_ids"] == [task.id]

    duplicate = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions/from-tasks",
        json={"source_task_ids": [task.id]},
        headers=testuser_token_headers,
    )
    assert duplicate.status_code == 409, duplicate.text


def test_dataset_version_rejects_inaccessible_source_task(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    dataset = _create_dataset(client, testuser_token_headers, name="权限校验数据集")
    other = _create_user(db, "dataset-source-owner@example.com")
    task = _seed_completed_task(db, user_id=other.id)

    response = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions/from-tasks",
        json={"source_task_ids": [task.id]},
        headers=testuser_token_headers,
    )
    assert response.status_code == 403, response.text
    assert "无权访问来源任务" in response.text


def test_dataset_version_backfills_complete_legacy_rect_contract(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    dataset = _create_dataset(
        client, testuser_token_headers, name="历史任务契约迁移数据集"
    )
    task = Task(
        name="历史 rectTool 任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
        config=json.dumps(
            {
                "tools": [
                    {
                        "tool": "rectTool",
                        "config": {
                            "attributes": [
                                {
                                    "key": "历史类别",
                                    "value": "legacy_category",
                                    "color": "#123456",
                                }
                            ]
                        },
                    }
                ]
            }
        ),
    )
    db.add(task)
    db.flush()
    db.add(
        AnnotationLabel(
            label_key="历史类别",
            label_value="legacy_category",
            normalized_label_value="legacy_category",
            annotation_kind="bbox",
            color="#123456",
            attributes_json="[]",
            status="active",
            version=1,
            created_by=1,
            updated_by=1,
        )
    )
    db.flush()
    attachment = TaskAttachment(
        task_id=task.id,
        filename="legacy.jpg",
        path=f"tasks/{task.id}/legacy.jpg",
        url=f"/api/v1/tasks/attachment/tasks/{task.id}/legacy.jpg",
        created_by=1,
        updated_by=1,
    )
    db.add(attachment)
    db.flush()
    db.add(
        TaskSample(
            task_id=task.id,
            file_id=attachment.id,
            created_by=1,
            updated_by=1,
            state=SampleState.DONE.value,
            data=json.dumps({"rect": [{"label": "legacy_category"}]}),
        )
    )
    db.commit()

    response = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions/from-tasks",
        json={"source_task_ids": [task.id]},
        headers=testuser_token_headers,
    )
    assert response.status_code == 201, response.text
    db.expire_all()
    assert (
        db.query(TaskAnnotationContract)
        .filter(TaskAnnotationContract.task_id == task.id)
        .count()
        == 1
    )


def test_dataset_version_rejects_partial_legacy_rect_contract(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    dataset = _create_dataset(
        client, testuser_token_headers, name="历史任务不完整契约数据集"
    )
    task = Task(
        name="历史 rectTool 部分类别任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
        config=json.dumps(
            {
                "tools": [
                    {
                        "tool": "rectTool",
                        "config": {
                            "attributes": [
                                {"key": "存在类别", "value": "present"},
                                {"key": "缺失类别", "value": "missing"},
                            ]
                        },
                    }
                ]
            }
        ),
    )
    db.add(task)
    db.flush()
    db.add(
        AnnotationLabel(
            label_key="存在类别",
            label_value="present",
            normalized_label_value="present",
            annotation_kind="bbox",
            color="#123456",
            attributes_json="[]",
            status="active",
            version=1,
            created_by=1,
            updated_by=1,
        )
    )
    db.flush()
    attachment = TaskAttachment(
        task_id=task.id,
        filename="partial.jpg",
        path=f"tasks/{task.id}/partial.jpg",
        url=f"/api/v1/tasks/attachment/tasks/{task.id}/partial.jpg",
        created_by=1,
        updated_by=1,
    )
    db.add(attachment)
    db.flush()
    db.add(
        TaskSample(
            task_id=task.id,
            file_id=attachment.id,
            created_by=1,
            updated_by=1,
            state=SampleState.DONE.value,
            data=json.dumps({"rect": [{"label": "present"}]}),
        )
    )
    db.commit()

    response = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions/from-tasks",
        json={"source_task_ids": [task.id]},
        headers=testuser_token_headers,
    )
    assert response.status_code == 422, response.text
    assert "缺少有效标签契约" in response.text


def test_dataset_version_rejects_unfinished_source_task(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    dataset = _create_dataset(client, testuser_token_headers, name="未完成任务数据集")
    task = _seed_completed_task(db, user_id=1, status="INPROGRESS")
    response = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions/from-tasks",
        json={"source_task_ids": [task.id]},
        headers=testuser_token_headers,
    )
    assert response.status_code == 422, response.text
    assert "尚未完成" in response.text


def test_dataset_category_is_bound_and_snapshotted(
    client: TestClient, testadmin_token_headers: dict[str, str]
):
    category = client.post(
        "/api/v1/annotation-taxonomy/dataset-categories",
        json={"category_key": "datasetGovernanceTest", "name": "数据治理测试"},
        headers=testadmin_token_headers,
    )
    assert category.status_code == 201, category.text
    dataset = _create_dataset(
        client,
        testadmin_token_headers,
        name="有分类数据集",
        dataset_category_id=category.json()["data"]["id"],
    )
    version = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        json=_version_payload("3"),
        headers=testadmin_token_headers,
    )
    assert version.status_code == 201, version.text
    assert (
        '"category_key":"datasetGovernanceTest"'
        in version.json()["data"]["dataset_category_snapshot_json"]
    )


def test_dataset_access_uses_owner_and_task_collaboration(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    dataset = _create_dataset(client, testuser_token_headers, name="任务协作数据集")
    collaborator = _create_user(db, "dataset-collaborator@example.com")
    outsider = _create_user(db, "dataset-outsider@example.com")
    task = _seed_completed_task(db, user_id=1, dataset_id=dataset["id"])
    db.add(TaskCollaborator(task_id=task.id, user_id=collaborator.id))
    db.commit()

    collaborator_headers = _login(client, collaborator.username)
    outsider_headers = _login(client, outsider.username)
    assert (
        client.get(
            f"/api/v1/datasets/{dataset['id']}", headers=collaborator_headers
        ).status_code
        == 200
    )
    denied = client.post(
        f"/api/v1/datasets/{dataset['id']}/versions",
        json=_version_payload("4"),
        headers=collaborator_headers,
    )
    assert denied.status_code == 403
    assert (
        client.get(
            f"/api/v1/datasets/{dataset['id']}", headers=outsider_headers
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/datasets", headers=outsider_headers).json()["meta_data"][
            "total"
        ]
        == 0
    )


def test_admin_lists_all_datasets_without_project_filter(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    owner_dataset = _create_dataset(
        client, testuser_token_headers, name="标注人员数据集"
    )
    other = _create_user(db, "dataset-list-owner@example.com")
    other_dataset = _create_dataset(
        client, _login(client, other.username), name="其他标注人员数据集"
    )

    owner_rows = client.get(
        "/api/v1/datasets?page=0&size=100", headers=testuser_token_headers
    ).json()["data"]
    assert [row["id"] for row in owner_rows] == [owner_dataset["id"]]
    admin_rows = client.get(
        "/api/v1/datasets?page=0&size=100", headers=testadmin_token_headers
    ).json()["data"]
    assert {row["id"] for row in admin_rows} == {
        owner_dataset["id"],
        other_dataset["id"],
    }


def test_dataset_openapi_contains_no_project_parameter(client: TestClient):
    schema = client.get("/openapi.json").json()
    create_operation = schema["paths"]["/api/v1/datasets"]["post"]
    list_operation = schema["paths"]["/api/v1/datasets"]["get"]
    assert all(
        item["name"] != "project_id" for item in create_operation.get("parameters", [])
    )
    assert all(
        item["name"] != "project_id" for item in list_operation.get("parameters", [])
    )
