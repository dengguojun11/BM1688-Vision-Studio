"""evaluation-hub API 聚合测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture
def yolo_train_enabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)


@pytest.fixture
def finished_task(db):
    from labelu.internal.domain.models.task import Task

    with db.begin():
        t = Task(
            name="pytest-eval-hub",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(t)
        db.flush()
        task_id = t.id
    yield task_id


def test_evaluation_hub_api(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    finished_task,
    db,
):
    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            metrics_summary='{"dataset":{"train":1,"val":1,"test":1}}',
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.get(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/evaluation-hub",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == finished_task
    assert data["job_id"] == job_id
    assert isinstance(data["reports"], list)
    assert data["job_status"] == YoloTrainStatus.COMPLETED.value
