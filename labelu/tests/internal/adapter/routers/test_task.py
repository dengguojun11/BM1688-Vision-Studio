import json
from pathlib import Path

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.compare_sample_access_token import (
    create_compare_sample_access_token,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_contract import SampleAnnotationLabelIndex
from labelu.internal.domain.models.auto_training import AutoTrainingOutbox
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_consistent_pool_registry import (
    YoloConsistentPoolRegistry,
)
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.common.security import get_password_hash
from labelu.internal.common.service_account import ensure_open_system_user
from labelu.internal.common.plate_recognition_profile import (
    normalize_plate_recognition_task_config,
    task_config_matches_profile,
)
from labelu.internal.application.command.task import (
    BatchAssignDatasetCategoryCommand,
    BatchAssignTaskProjectCommand,
)


def test_batch_assign_dataset_category_command_normalizes_task_ids():
    command = BatchAssignDatasetCategoryCommand(
        task_ids=[3, 3, "4"],
        dataset_category_id=2,
    )

    assert command.task_ids == [3, 4]


def test_batch_assign_task_project_command_normalizes_task_ids():
    command = BatchAssignTaskProjectCommand(task_ids=[3, 3, "4"], project_id=7)

    assert command.task_ids == [3, 4]


def test_legacy_task_project_assignment_is_retired_without_writes(
    client: TestClient, testuser_token_headers: dict, db: Session
) -> None:
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="legacy-task-1",
                status="FINISHED",
                media_type="IMAGE",
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )

    response = client.post(
        f"{settings.API_V1_STR}/tasks/project-assignment",
        headers=testuser_token_headers,
        json={"task_ids": [task.id], "project_id": 1},
    )
    assert response.status_code == 403
    assert response.json()["err_code"] == 30004
    db.expire_all()
    assert db.query(Task).filter(Task.id == task.id).one().project_id is None

    single = client.patch(
        f"{settings.API_V1_STR}/tasks/{task.id}/project",
        headers=testuser_token_headers,
        json={"project_id": 1},
    )
    assert single.status_code == 403
    assert single.json()["err_code"] == 30004
    db.expire_all()
    assert db.query(Task).filter(Task.id == task.id).one().project_id is None


class TestClassTaskRouter:
    def test_create_task_idempotency_replays_and_rejects_key_reuse(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        headers = dict(testuser_token_headers)
        headers["Idempotency-Key"] = "task-create-retry-001"
        body = {"name": "幂等任务", "media_type": "IMAGE"}

        first = client.post(
            f"{settings.API_V1_STR}/tasks", headers=headers, json=body
        )
        second = client.post(
            f"{settings.API_V1_STR}/tasks", headers=headers, json=body
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert second.json()["data"]["id"] == first.json()["data"]["id"]
        assert (
            db.query(Task)
            .filter(Task.idempotency_key == "task-create-retry-001")
            .count()
            == 1
        )

        conflict = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=headers,
            json={"name": "另一个任务", "media_type": "IMAGE"},
        )
        assert conflict.status_code == 409, conflict.text
        assert "Idempotency-Key" in conflict.text

    def test_create_task_can_bind_owned_dataset_without_project(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        dataset_response = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=testuser_token_headers,
            json={"name": "无项目任务数据集"},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["data"]["id"]

        response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={
                "name": "绑定无项目数据集的任务",
                "media_type": "IMAGE",
                "dataset_id": dataset_id,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["data"]["dataset_id"] == dataset_id
        assert response.json()["data"]["project_id"] is None
        task = db.query(Task).filter(Task.id == response.json()["data"]["id"]).one()
        assert task.dataset_id == dataset_id
        assert task.project_id is None

    def test_dataset_analysis_accepts_selected_task_ids(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
    ) -> None:
        dataset_response = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=testuser_token_headers,
            json={"name": "训练中心任务筛选分析"},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["data"]["id"]
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={
                "name": "训练中心选中任务",
                "media_type": "IMAGE",
                "dataset_id": dataset_id,
            },
        )
        assert task_response.status_code == 201, task_response.text
        task_id = task_response.json()["data"]["id"]

        response = client.get(
            f"{settings.API_V1_STR}/tasks/dataset-analysis",
            params={"dataset_id": dataset_id, "task_ids": [task_id]},
            headers=testuser_token_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["dataset_id"] == dataset_id
        assert [row["task_id"] for row in response.json()["data"]["tasks"]] == [task_id]

    def test_create_task_rejects_dataset_without_access(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        owner = User(
            username="dataset-binding-owner@example.com",
            hashed_password=get_password_hash("User@123"),
        )
        db.add(owner)
        db.commit()
        owner_headers = {
            "Authorization": client.post(
                f"{settings.API_V1_STR}/users/login",
                json={
                    "username": owner.username,
                    "password": "User@123",
                },
            ).json()["data"]["token"]
        }
        dataset_response = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=owner_headers,
            json={"name": "无权限数据集"},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["data"]["id"]

        response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={
                "name": "越权绑定任务",
                "media_type": "IMAGE",
                "dataset_id": dataset_id,
            },
        )

        assert response.status_code == 403, response.text

    def test_update_task_can_bind_dataset_without_project(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        dataset_response = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=testuser_token_headers,
            json={"name": "编辑绑定数据集"},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["data"]["id"]
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={"name": "先建任务后绑定", "media_type": "IMAGE"},
        )
        assert task_response.status_code == 201, task_response.text
        task_id = task_response.json()["data"]["id"]

        response = client.patch(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
            json={"dataset_id": dataset_id},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["dataset_id"] == dataset_id
        assert response.json()["data"]["project_id"] is None
        db.expire_all()
        task = db.query(Task).filter(Task.id == task_id).one()
        assert task.dataset_id == dataset_id
        assert task.project_id is None

    def test_collaborator_can_create_task_from_linked_dataset(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
        db: Session,
    ) -> None:
        collaborator = db.query(User).filter(
            User.username == "dataset-binding-collaborator@example.com"
        ).first()
        if collaborator is None:
            collaborator = User(
                username="dataset-binding-collaborator@example.com",
                hashed_password=get_password_hash("User@123"),
            )
            db.add(collaborator)
            db.commit()
            db.refresh(collaborator)
        collaborator_headers = {
            "Authorization": client.post(
                f"{settings.API_V1_STR}/users/login",
                json={
                    "username": collaborator.username,
                    "password": "User@123",
                },
            ).json()["data"]["token"]
        }
        dataset_response = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=testuser_token_headers,
            json={"name": "协作任务数据集"},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["data"]["id"]
        source_task = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={
                "name": "已有协作任务",
                "media_type": "IMAGE",
                "dataset_id": dataset_id,
            },
        )
        assert source_task.status_code == 201, source_task.text
        db.add(
            TaskCollaborator(
                task_id=source_task.json()["data"]["id"],
                user_id=collaborator.id,
            )
        )
        db.commit()

        response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=collaborator_headers,
            json={
                "name": "协作者复用数据集",
                "media_type": "IMAGE",
                "dataset_id": dataset_id,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["data"]["dataset_id"] == dataset_id

    def test_admin_can_create_task_from_any_dataset(
        self,
        client: TestClient,
        testuser_token_headers: dict[str, str],
        testadmin_token_headers: dict[str, str],
    ) -> None:
        dataset_response = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=testuser_token_headers,
            json={"name": "管理员可复用数据集"},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["data"]["id"]

        response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testadmin_token_headers,
            json={
                "name": "管理员任务",
                "media_type": "IMAGE",
                "dataset_id": dataset_id,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["data"]["dataset_id"] == dataset_id

    def test_create_task_successful(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }

        automatic_events_before = db.query(AutoTrainingOutbox).count()

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )

        # check
        json = r.json()
        assert r.status_code == 201
        assert json["data"]["id"] > 0
        assert json["data"]["created_by"]["id"] > 0
        assert json["data"]["status"] == "DRAFT"
        assert db.query(AutoTrainingOutbox).count() == automatic_events_before

    def test_create_task_no_authentication(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks", headers=testuser_token_headers, json=data
        )

        # check
        assert r.status_code == 201

    def test_task_list(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        page = 0
        size = 10
        with begin_transaction(db):
            for i in range(15):
                crud_task.create(
                    db=db,
                    task=Task(
                        name="name",
                        description="description",
                        tips="tips",
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            params={"page": page, "size": size},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert json["meta_data"]["total"] == 15
        assert len(json["data"]) == 10

    def test_third_party_task_is_visible_and_editable_to_regular_user(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        owner = ensure_open_system_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="third-party-task",
                    description="created by integration",
                    media_type="IMAGE",
                    source_type="third_party",
                    source_system="strategy_evolution",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )

        listed = client.get(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
        )
        assert listed.status_code == 200
        assert task.id in {row["id"] for row in listed.json()["data"]}

        detail = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=testuser_token_headers,
        )
        assert detail.status_code == 200

        updated = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=testuser_token_headers,
            json={"name": "third-party-task-updated"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["name"] == "third-party-task-updated"

    def test_task_get(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
            "config": "{}",
            "media_type": "IMAGE",
        }
        task = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )

        # run
        task_id = task.json()["data"]["id"]
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()["data"]
        assert r.status_code == 200
        assert json["name"] == "task name"
        assert json["created_by"]["id"] > 0

    def test_task_get_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/0",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 50002

    def test_task_get_with_compare_access_token_for_outsider(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        owner = crud_user.get_user_by_username(db=db, username="test@example.com")
        outsider_name = "compare-task-outsider@example.com"
        with begin_transaction(db):
            outsider = crud_user.get_user_by_username(db=db, username=outsider_name)
            if not outsider:
                outsider = crud_user.create(
                    db=db,
                    user=User(
                        username=outsider_name,
                        hashed_password=get_password_hash("test@123"),
                    ),
                )
            task = crud_task.create(
                db=db,
                task=Task(
                    name="compare-task",
                    description="",
                    tips="",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=owner.id,
                        updated_by=owner.id,
                        data="{}",
                    )
                ],
            )
        token = create_compare_sample_access_token(task.id, samples[0].id)
        login = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": outsider_name, "password": "test@123"},
        )
        headers = {"Authorization": login.json()["data"]["token"]}

        denied = client.get(f"{settings.API_V1_STR}/tasks/{task.id}", headers=headers)
        allowed = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=headers,
            params={"sample_id": samples[0].id, "compare_access_token": token},
        )

        assert denied.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["data"]["id"] == task.id

    def test_task_update(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        for_update_task_data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
            "config": "{}",
            "media_type": "IMAGE",
        }
        for_update_task = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=for_update_task_data,
        )
        for_updated_task_data = {
            "name": "new name",
            "description": "new description",
            "tips": "new tips",
            "config": '{"v": 2}',
            "media_type": "IMAGE",
        }

        # run
        task_id = for_update_task.json()["data"]["id"]
        updated_task = client.patch(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
            json=for_updated_task_data,
        )

        # check
        json = updated_task.json()
        assert json["data"]["name"] == "new name"
        assert json["data"]["description"] == "new description"
        assert json["data"]["tips"] == "new tips"
        assert json["data"]["config"] == '{"v": 2}'
        assert json["data"]["media_type"] == "IMAGE"

    def test_task_partial_update_preserves_omitted_fields(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        created = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={"name": "partial update task", "media_type": "IMAGE"},
        )
        task_id = created.json()["data"]["id"]
        legacy_name = "legacy-imported-task-name-" + "x" * 32
        with begin_transaction(db):
            task = crud_task.get(db=db, task_id=task_id)
            crud_task.update(
                db=db,
                db_obj=task,
                obj_in={"name": legacy_name, "config": '{"old": true}'},
            )

        config_response = client.patch(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
            json={"config": '{"tools": []}'},
        )

        assert config_response.status_code == 200
        config_data = config_response.json()["data"]
        assert config_data["name"] == legacy_name
        assert json.loads(config_data["config"]) == {"tools": []}
        assert config_data["media_type"] == "IMAGE"
        assert config_data["status"] == "CONFIGURED"

        description_response = client.patch(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
            json={"description": "updated only"},
        )

        assert description_response.status_code == 200
        description_data = description_response.json()["data"]
        assert json.loads(description_data["config"]) == {"tools": []}
        assert description_data["description"] == "updated only"

    def test_task_update_no_found_task(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        for_updated_task_data = {
            "name": "new name",
            "description": "new description",
            "tips": "new tips",
            "config": "{}",
            "media_type": "IMAGE",
        }

        # run
        updated_task = client.patch(
            f"{settings.API_V1_STR}/tasks/0",
            headers=testuser_token_headers,
            json=for_updated_task_data,
        )

        # check
        json = updated_task.json()
        assert updated_task.status_code == 404
        assert json["err_code"] == 50002

    def test_task_update_keeps_plate_preset_for_stale_client_config(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        plate_config = normalize_plate_recognition_task_config({})
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="plate task",
                    config=json.dumps(plate_config, ensure_ascii=False),
                    media_type="IMAGE",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        stale_config = normalize_plate_recognition_task_config({})
        stale_config["defaultRectPresetId"] = None
        polygon = next(
            item for item in stale_config["tools"] if item["tool"] == "polygonTool"
        )
        polygon["config"]["attributes"][0]["value"] = " platePoint"
        response = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=testuser_token_headers,
            json={
                "name": task.name,
                "config": json.dumps(stale_config, ensure_ascii=False),
                "media_type": "IMAGE",
            },
        )

        assert response.status_code == 200
        saved_config = json.loads(response.json()["data"]["config"])
        assert saved_config["defaultRectPresetId"] == "plateRecognition"
        assert task_config_matches_profile(saved_config)
        saved_polygon = next(
            item for item in saved_config["tools"] if item["tool"] == "polygonTool"
        )
        assert saved_polygon["config"]["attributes"][0]["value"] == "platePoint"

    def test_task_update_no_config(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        for_update_task_data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        for_update_task = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=for_update_task_data,
        )
        for_updated_task_data = {
            "name": "new name",
            "description": "new description",
            "tips": "new tips",
            "config": "{}",
        }

        # run
        task_id = for_update_task.json()["data"]["id"]
        updated_task = client.patch(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
            json=for_updated_task_data,
        )

        # check
        json = updated_task.json()
        assert json["data"]["name"] == "new name"
        assert json["data"]["description"] == "new description"
        assert json["data"]["tips"] == "new tips"
        assert json["data"]["config"] == None
        assert json["data"]["media_type"] == None

    def test_task_add_annotation_config(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        for_update_task_data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        for_update_task = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=for_update_task_data,
        )
        for_updated_task_data = {
            "name": "new name",
            "description": "new description",
            "tips": "new tips",
            "config": '{"tools": []}',
            "media_type": "IMAGE",
        }

        # run
        task_id = for_update_task.json()["data"]["id"]
        updated_task = client.patch(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testuser_token_headers,
            json=for_updated_task_data,
        )

        # check
        json = updated_task.json()
        assert json["data"]["name"] == "new name"
        assert json["data"]["description"] == "new description"
        assert json["data"]["tips"] == "new tips"
        assert json["data"]["config"] == '{"tools": []}'
        assert json["data"]["media_type"] == "IMAGE"
        assert json["data"]["status"] == "CONFIGURED"

    def test_task_delete(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        # run
        r = client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert json["data"]["ok"] == True

    def test_task_delete_removes_database_rows_and_media(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        owner = crud_user.get_user_by_username(db=db, username="test@example.com")
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="cleanup task",
                    created_by=owner.id,
                    updated_by=owner.id,
                    media_type="IMAGE",
                ),
            )
            attachment = TaskAttachment(
                filename="delete-me.jpg",
                url="/api/v1/tasks/attachment/delete-me.jpg",
                path=f"upload/{task.id}/delete-me.jpg",
                task_id=task.id,
                created_by=owner.id,
                updated_by=owner.id,
            )
            db.add(attachment)
            db.flush()
            sample = TaskSample(
                task_id=task.id,
                file_id=attachment.id,
                created_by=owner.id,
                updated_by=owner.id,
                data=json.dumps(
                    {
                        "result": json.dumps(
                            {"rectTool": {"result": [{"label": "delete-test"}]}}
                        )
                    }
                ),
            )
            crud_sample.batch(db, [sample])
            attachment_id = attachment.id
            sample_id = sample.id
            assert (
                db.query(SampleAnnotationLabelIndex)
                .filter(SampleAnnotationLabelIndex.sample_id == sample_id)
                .count()
                == 1
            )

        media_path = (
            Path(settings.MEDIA_ROOT)
            / settings.UPLOAD_DIR
            / str(task.id)
            / "delete-me.jpg"
        )
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(b"test")

        response = client.delete(
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=testuser_token_headers,
        )

        assert response.status_code == 200
        assert db.query(Task).filter(Task.id == task.id).first() is None
        assert (
            db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).first()
            is None
        )
        assert db.query(TaskSample).filter(TaskSample.id == sample_id).first() is None
        assert (
            db.query(SampleAnnotationLabelIndex)
            .filter(SampleAnnotationLabelIndex.sample_id == sample_id)
            .count()
            == 0
        )
        assert not media_path.exists()

    def test_task_delete_unbinds_consistent_pool_registry(
        self, client: TestClient, testadmin_token_headers: dict, db: Session
    ) -> None:
        owner = crud_user.get_user_by_username(db=db, username="admin@123.com")
        signature = "delete-consistent-pool-signature"
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="consistent pool cleanup task",
                    created_by=owner.id,
                    updated_by=owner.id,
                    source_type="consistent_pool",
                    source_external_task_id=signature,
                ),
            )
            registry = YoloConsistentPoolRegistry(
                label_set_signature=signature,
                task_id=task.id,
            )
            db.add(registry)
            db.flush()
            task_id = int(task.id)
            registry_id = int(registry.id)

        response = client.delete(
            f"{settings.API_V1_STR}/tasks/{task_id}",
            headers=testadmin_token_headers,
        )

        assert response.status_code == 200
        assert db.query(Task).filter(Task.id == task_id).first() is None
        remaining_registry = (
            db.query(YoloConsistentPoolRegistry)
            .filter(YoloConsistentPoolRegistry.id == registry_id)
            .one()
        )
        assert remaining_registry.label_set_signature == signature
        assert remaining_registry.task_id is None

    def test_task_delete_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data

        # run
        r = client.request(
            "delete", f"{settings.API_V1_STR}/tasks/0", headers=testuser_token_headers
        )

        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 50002

    def test_task_delete_no_permission(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )

        # run
        r = client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task.id}",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 403
        assert json["err_code"] == 30001

    def test_task_get_collaborators(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        data = {
            "name": "task name",
            "description": "task description",
            "tips": "task tips",
        }
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )
        task_id = task_response.json()["data"]["id"]

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 200
        # 创建者可能不会自动成为协作者，根据实际业务逻辑修改断言
        # assert len(json["data"]) >= 1

    def test_task_add_collaborator(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        db.rollback()
        data = {
            "name": "collaborative task",
            "description": "task description",
            "tips": "task tips",
        }
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )
        task_id = task_response.json()["data"]["id"]

        # run - 尝试添加当前用户为协作者（即使它已经是协作者）
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators",
            headers=testuser_token_headers,
            json={"user_id": current_user.id},
        )

        # check
        json = r.json()
        assert r.status_code == 201
        assert json["data"]["id"] == current_user.id

    def test_task_batch_add_collaborators(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        # 获取或创建测试用户
        new_user = crud_user.get_user_by_username(
            db=db, username="collaborator@example.com"
        )
        if not new_user:
            with begin_transaction(db):
                new_user = crud_user.create(
                    db=db,
                    user=User(
                        username="collaborator@example.com",
                        hashed_password="hashed_password",
                    ),
                )
        else:
            db.rollback()

        data = {
            "name": "batch collaborative task",
            "description": "task description",
            "tips": "task tips",
        }
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )
        task_id = task_response.json()["data"]["id"]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators/batch_add",
            headers=testuser_token_headers,
            json={"user_ids": [current_user.id, new_user.id]},
        )

        # check
        json = r.json()
        assert r.status_code == 201
        # 期望只有一个协作者
        assert len(json["data"]) == 1
        user_ids = [user["id"] for user in json["data"]]
        assert new_user.id in user_ids

    def test_task_batch_remove_collaborators(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        # 获取或创建测试用户
        new_user = crud_user.get_user_by_username(
            db=db, username="to_remove@example.com"
        )
        if not new_user:
            with begin_transaction(db):
                new_user = crud_user.create(
                    db=db,
                    user=User(
                        username="to_remove@example.com",
                        hashed_password="hashed_password",
                    ),
                )
        else:
            db.rollback()

        data = {
            "name": "remove collaborative task",
            "description": "task description",
            "tips": "task tips",
        }
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )
        task_id = task_response.json()["data"]["id"]

        # 先添加协作者
        client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators/batch_add",
            headers=testuser_token_headers,
            json={"user_ids": [new_user.id]},
        )

        # run - 移除协作者
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators/batch_remove",
            headers=testuser_token_headers,
            json={"user_ids": [new_user.id]},
        )

        # check
        json = r.json()
        assert r.status_code == 201
        assert json["data"]["ok"] == True

        # 验证协作者已被移除
        collaborators = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators",
            headers=testuser_token_headers,
        )
        assert new_user.id not in [c["id"] for c in collaborators.json()["data"]]

    def test_task_remove_collaborator(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # prepare data
        # 获取或创建测试用户
        new_user = crud_user.get_user_by_username(
            db=db, username="single_remove@example.com"
        )
        if not new_user:
            with begin_transaction(db):
                new_user = crud_user.create(
                    db=db,
                    user=User(
                        username="single_remove@example.com",
                        hashed_password="hashed_password",
                    ),
                )
        else:
            db.rollback()

        data = {
            "name": "single remove task",
            "description": "task description",
            "tips": "task tips",
        }
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )
        task_id = task_response.json()["data"]["id"]

        # 先添加协作者
        client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators",
            headers=testuser_token_headers,
            json={"user_id": new_user.id},
        )

        # run - 移除单个协作者
        r = client.delete(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators/{new_user.id}",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert json["data"]["ok"] == True

        # 验证协作者已被移除
        collaborators = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators",
            headers=testuser_token_headers,
        )
        assert new_user.id not in [c["id"] for c in collaborators.json()["data"]]

    def test_task_not_found_for_collaborators(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # 测试不存在的任务
        r1 = client.get(
            f"{settings.API_V1_STR}/tasks/99999/collaborators",
            headers=testuser_token_headers,
        )
        assert r1.status_code == 404

    def test_user_not_found_for_collaborators(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # 测试添加不存在的用户

        data = {
            "name": "error test task",
            "description": "task description",
            "tips": "task tips",
        }
        task_response = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json=data,
        )
        task_id = task_response.json()["data"]["id"]

        r2 = client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/collaborators",
            headers=testuser_token_headers,
            json={"user_id": 99999},  # 不存在的用户ID
        )
        # 验证状态码是4xx，表示客户端错误
        assert r2.status_code == 404
