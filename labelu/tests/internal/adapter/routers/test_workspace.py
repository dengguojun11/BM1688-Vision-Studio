from fastapi.testclient import TestClient

from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.published_model_catalog import PublishedModelCatalog
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def _users(db):
    annotator = db.query(User).filter(User.username == "test@example.com").one()
    administrator = db.query(User).filter(User.username == "admin@123.com").one()
    return annotator, administrator


def _task(*, owner_id: int, name: str, status: str = "CONFIGURED") -> Task:
    return Task(
        name=name,
        media_type=MediaType.IMAGE.value,
        status=status,
        created_by=owner_id,
        updated_by=owner_id,
    )


def test_workspace_summary_uses_task_permissions_without_project_scope(
    client: TestClient, db, testuser_token_headers: dict[str, str]
):
    annotator, administrator = _users(db)
    with begin_transaction(db):
        db.add(_task(owner_id=annotator.id, name="本人任务"))
        db.add(_task(owner_id=administrator.id, name="其他人员任务"))

    response = client.get("/api/v1/workspace/summary", headers=testuser_token_headers)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["task_count"] == 1
    assert payload["pending_annotation_task_count"] == 1
    assert "project_count" not in payload
    assert "deployment_count" not in payload
    assert "generated_at" in payload


def test_workspace_summary_requires_authentication(client: TestClient):
    assert client.get("/api/v1/workspace/summary").status_code == 403


def test_workspace_summary_counts_real_training_and_published_models(
    client: TestClient, db, testuser_token_headers: dict[str, str]
):
    annotator, _ = _users(db)
    with begin_transaction(db):
        task = _task(owner_id=annotator.id, name="训练任务")
        db.add(task)
        db.flush()
        db.add_all(
            [
                YoloTrainJob(
                    task_id=task.id,
                    created_by=annotator.id,
                    status=YoloTrainStatus.PROCESSING.value,
                ),
                YoloTrainJob(
                    task_id=task.id,
                    created_by=annotator.id,
                    status=YoloTrainStatus.COMPLETED.value,
                ),
                YoloTrainJob(
                    task_id=task.id,
                    created_by=annotator.id,
                    status=YoloTrainStatus.FAILED.value,
                ),
                PublishedModelCatalog(
                    public_model_id="aicm-workspace-1",
                    task_id=task.id,
                    name="安全帽检测",
                    display_name="安全帽检测",
                    download_enabled=False,
                ),
            ]
        )

    response = client.get("/api/v1/workspace/summary", headers=testuser_token_headers)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["active_training_count"] == 1
    assert payload["failed_training_count"] == 1
    assert payload["model_count"] == 1
    assert payload["published_model_count"] == 1
    assert payload["download_disabled_model_count"] == 1


def test_workspace_activity_is_database_paged_and_task_scoped(
    client: TestClient, db, testuser_token_headers: dict[str, str]
):
    annotator, administrator = _users(db)
    with begin_transaction(db):
        own_task = _task(owner_id=annotator.id, name="本人流水线")
        other_task = _task(owner_id=administrator.id, name="不可见流水线")
        db.add_all([own_task, other_task])
        db.flush()
        own_train = YoloTrainJob(
            task_id=own_task.id,
            created_by=annotator.id,
            status=YoloTrainStatus.FAILED.value,
            error_message="训练节点不可用",
        )
        other_train = YoloTrainJob(
            task_id=other_task.id,
            created_by=administrator.id,
            status=YoloTrainStatus.FAILED.value,
        )
        db.add_all([own_train, other_train])
        db.flush()
        export = YoloBm1688Export(
            user_id=annotator.id,
            task_id=own_task.id,
            yolo_train_job_id=own_train.id,
            status="FAILED",
            error_message="编译节点不可用",
        )
        db.add(export)
        db.flush()
        db.add(
            YoloEdgeValidationJob(
                user_id=annotator.id,
                task_id=own_task.id,
                export_id=export.id,
                status="COMPLETED",
            )
        )

    alerts = client.get(
        "/api/v1/workspace/alerts?page=0&size=1",
        headers=testuser_token_headers,
    )
    assert alerts.status_code == 200, alerts.text
    assert alerts.json()["meta_data"]["total"] == 2
    assert len(alerts.json()["data"]) == 1
    assert "project_id" not in alerts.json()["data"][0]

    filtered_alerts = client.get(
        "/api/v1/workspace/alerts?page=0&size=10&q=%E8%AE%AD%E7%BB%83%E8%8A%82%E7%82%B9%E4%B8%8D%E5%8F%AF%E7%94%A8",
        headers=testuser_token_headers,
    )
    assert filtered_alerts.status_code == 200, filtered_alerts.text
    assert filtered_alerts.json()["meta_data"]["total"] == 1

    recent = client.get(
        "/api/v1/workspace/recent-jobs?page=0&size=10",
        headers=testuser_token_headers,
    )
    assert recent.status_code == 200, recent.text
    rows = recent.json()["data"]
    assert len(rows) == 3
    assert {row["job_type"] for row in rows} == {
        "TRAINING",
        "BM1688_COMPILE",
        "EDGE_VALIDATION",
    }
    assert all(row["task_id"] == own_task.id for row in rows)

    filtered_recent = client.get(
        "/api/v1/workspace/recent-jobs?page=0&size=10&q=%E7%BC%96%E8%AF%91%E8%8A%82%E7%82%B9%E4%B8%8D%E5%8F%AF%E7%94%A8",
        headers=testuser_token_headers,
    )
    assert filtered_recent.status_code == 200, filtered_recent.text
    assert filtered_recent.json()["meta_data"]["total"] == 1

    task_name_search = client.get(
        "/api/v1/workspace/recent-jobs?page=0&size=10&q=%E6%9C%AC%E4%BA%BA%E6%B5%81%E6%B0%B4%E7%BA%BF",
        headers=testuser_token_headers,
    )
    assert task_name_search.status_code == 200, task_name_search.text
    assert task_name_search.json()["meta_data"]["total"] == 3
