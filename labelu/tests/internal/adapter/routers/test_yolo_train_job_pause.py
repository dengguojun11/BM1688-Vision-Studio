"""训练作业暂停 API。"""
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
            name="pytest-yolo-pause",
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


def test_pause_processing_job(
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
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/pause",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "CANCELLED"

    r2 = client.get(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["status"] == "CANCELLED"


def test_pause_completed_job_rejected(
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
            weights_path="/tmp/fake.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/pause",
        headers=testuser_token_headers,
    )
    assert r.status_code == 400
