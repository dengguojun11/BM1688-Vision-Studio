from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_yolo_train_analysis_endpoint_returns_current_user_job(
    client: TestClient,
    testuser_token_headers,
    db,
    tmp_path,
    monkeypatch,
):
    media = tmp_path / "media"
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(media))
    with db.begin():
        task = Task(
            name="analysis-task",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
            epochs=10,
        )
        db.add(job)
        db.flush()
        job_id = job.id

    train_dir = media / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}" / "train"
    train_dir.mkdir(parents=True)
    (train_dir / "results.csv").write_text(
        "epoch,metrics/mAP50-95(B)\n1,0.5\n",
        encoding="utf-8",
    )

    r = client.get(
        f"/api/v1/yolo-train-jobs/{job_id}/analysis",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["job"]["id"] == job_id
    assert data["series"]["map50_95"][0]["value"] == 0.5


def test_yolo_train_analysis_asset_endpoint_rejects_unknown_asset(
    client: TestClient,
    testuser_token_headers,
    db,
    tmp_path,
    monkeypatch,
):
    media = tmp_path / "media"
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(media))
    with db.begin():
        task = Task(
            name="analysis-task-assets",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            epochs=10,
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.get(
        f"/api/v1/yolo-train-jobs/{job_id}/analysis/assets/..%2Fsecret.png",
        headers=testuser_token_headers,
    )

    assert r.status_code == 404
