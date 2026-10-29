"""迭代训练入口需要的作业列表字段。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_global_job_list_returns_hyperparams_for_iteration_prefill(
    client: TestClient,
    testuser_token_headers,
    db,
):
    with db.begin():
        task = Task(
            name="pytest-yolo-iteration",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        db.add(
            YoloTrainJob(
                task_id=task.id,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
                model_weights="yolo11n.pt",
                epochs=24,
                imgsz=704,
                weights_path="/tmp/best.pt",
                category="夜间场景",
                hyperparams_json=json.dumps({"batch": 4, "cos_lr": True}, ensure_ascii=False),
            )
        )

    r = client.get("/api/v1/yolo-train-jobs?page=0&size=20", headers=testuser_token_headers)

    assert r.status_code == 200
    row = next(x for x in r.json()["data"] if x["task_id"] == task.id)
    assert row["hyperparams"] == {"batch": 4, "cos_lr": True}
