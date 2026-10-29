"""训练作业停止检测与继续运行 API。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture
def yolo_train_enabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def clear_active_train_jobs():
    from labelu.internal.application.service import yolo_train as yolo_train_service

    yolo_train_service._ACTIVE_TRAIN_JOBS.clear()
    yield
    yolo_train_service._ACTIVE_TRAIN_JOBS.clear()


@pytest.fixture
def finished_task(db):
    from labelu.internal.domain.models.task import Task

    with db.begin():
        t = Task(
            name="pytest-yolo-restart",
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


def test_processing_job_reports_stopped_when_runtime_pid_not_on_nvidia(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    monkeypatch.setattr(yolo_train_service, "_nvidia_compute_pids", lambda: {111})
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-77"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 999}),
        encoding="utf-8",
    )

    with db.begin():
        job = YoloTrainJob(
            id=77,
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
            epochs=12,
            imgsz=640,
        )
        db.add(job)

    r = client.get(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/77",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["runtime_status"] == "STOPPED"
    assert data["runtime_pid"] == 999
    assert data["is_restartable"] is True
    assert "NVIDIA" in data["restart_reason"]


def test_processing_job_running_when_gpu_on_child_process(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    """双卡训练时 GPU 在子进程；主进程 runtime_pid 不在 nvidia-smi 但应判为 RUNNING。"""
    from labelu.internal.application.service import yolo_train as yolo_train_service

    parent_pid = 3700216
    child_pid = 3762408
    monkeypatch.setattr(yolo_train_service, "_nvidia_compute_pids", lambda: {child_pid})
    monkeypatch.setattr(
        yolo_train_service,
        "_linux_process_tree_pids",
        lambda pid: {pid, child_pid} if pid == parent_pid else {pid},
    )
    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-78"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": parent_pid}),
        encoding="utf-8",
    )

    with db.begin():
        job = YoloTrainJob(
            id=78,
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
            epochs=12,
            imgsz=640,
        )
        db.add(job)

    r = client.get(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/78",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["runtime_status"] == "RUNNING"
    assert data["runtime_pid"] == parent_pid


def test_cancelled_job_can_resume_from_last_checkpoint(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    launched = []

    def fake_submit(job_id, task_id, cmd, *, resume=False):
        yolo_train_service._ACTIVE_TRAIN_JOBS.add(int(job_id))
        launched.append((job_id, task_id, cmd, resume))

    monkeypatch.setattr(
        yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True
    )
    monkeypatch.setattr(yolo_train_service, "_submit_yolo_train_job", fake_submit)

    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-88"
    last_pt = work / "train" / "weights" / "last.pt"
    last_pt.parent.mkdir(parents=True, exist_ok=True)
    last_pt.write_bytes(b"fake-last-pt")
    stop_flag = work / "stop_requested"
    stop_flag.write_text("1", encoding="utf-8")

    with db.begin():
        job = YoloTrainJob(
            id=88,
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11s.pt",
            epochs=12,
            imgsz=704,
            category="烟火",
            hyperparams_json=json.dumps({"batch": 4}, ensure_ascii=False),
        )
        db.add(job)
        db.flush()
        old_job_id = job.id

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{old_job_id}/restart",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["id"] == old_job_id
    assert data["task_id"] == finished_task
    assert data["model_weights"] == "yolo11s.pt"
    assert data["epochs"] == 12
    assert data["imgsz"] == 704
    assert data["category"] == "烟火"
    assert data["hyperparams"] == {"batch": 4}
    assert data["runtime_status"] == "RUNNING"
    assert data["is_restartable"] is False
    assert data["status"] == "PROCESSING"
    assert not stop_flag.exists()
    assert len(launched) == 1
    assert launched[0][0] == old_job_id
    assert launched[0][3] is True


def test_cancelled_job_can_restart_without_last_checkpoint(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    launched = []

    def fake_submit(job_id, task_id, cmd, *, resume=False):
        yolo_train_service._ACTIVE_TRAIN_JOBS.add(int(job_id))
        launched.append((job_id, task_id, cmd, resume))

    monkeypatch.setattr(
        yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True
    )
    monkeypatch.setattr(yolo_train_service, "_submit_yolo_train_job", fake_submit)

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11s.pt",
            epochs=12,
            imgsz=704,
            category="烟火",
            hyperparams_json=json.dumps({"batch": 4}, ensure_ascii=False),
        )
        db.add(job)
        db.flush()
        job_id = job.id

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/restart",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["id"] == job_id
    assert data["status"] == "PROCESSING"
    assert data["runtime_status"] == "RUNNING"
    assert data["model_weights"] == "yolo11s.pt"
    assert data["epochs"] == 12
    assert data["imgsz"] == 704
    assert data["category"] == "烟火"
    assert data["hyperparams"] == {"batch": 4}
    assert len(launched) == 1
    assert launched[0][0] == job_id
    assert launched[0][3] is False


def test_cancelled_plate_job_restarts_with_component_resume(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    launched = []

    def fake_submit(job_id, task_id, cmd, *, resume=False):
        yolo_train_service._ACTIVE_TRAIN_JOBS.add(int(job_id))
        launched.append((job_id, task_id, cmd, resume))

    monkeypatch.setattr(
        yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True
    )
    monkeypatch.setattr(yolo_train_service, "_submit_yolo_train_job", fake_submit)

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            job_type="plate_recognition",
            model_weights="yolov8n-pose.pt",
            epochs=100,
            imgsz=640,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/restart",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "PROCESSING"
    assert data["runtime_status"] == "RUNNING"
    assert launched[0][0] == job_id
    assert launched[0][3] is True


def test_cancelled_job_is_not_restartable_until_child_exits(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            job_type="plate_recognition",
            model_weights="yolov8n-pose.pt",
            epochs=100,
            imgsz=640,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 43210}),
        encoding="utf-8",
    )
    yolo_train_service._ACTIVE_TRAIN_JOBS.add(job_id)
    r = client.get(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["runtime_status"] == "RUNNING"
    assert data["is_restartable"] is False
    assert "正在停止" in data["restart_reason"]


def test_pause_plate_job_terminates_recorded_child_pid(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    with db.begin():
        job = YoloTrainJob(
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            job_type="plate_recognition",
            model_weights="yolov8n-pose.pt",
            epochs=100,
            imgsz=640,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 43210}),
        encoding="utf-8",
    )
    terminated = []
    monkeypatch.setattr(
        yolo_train_service,
        "_terminate_train_process_tree",
        lambda stopped_job_id, runtime_pid: terminated.append(
            (stopped_job_id, runtime_pid)
        ),
    )
    yolo_train_service._ACTIVE_TRAIN_JOBS.add(job_id)

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/pause",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "CANCELLED"
    assert data["runtime_status"] == "RUNNING"
    assert data["is_restartable"] is False
    assert terminated == [(job_id, 43210)]
    phase = json.loads((work / "phase.json").read_text(encoding="utf-8"))
    assert phase["runtime_pid"] == 43210


def test_cancelled_job_manual_restart_clears_stale_active_marker(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    media_root,
    finished_task,
    db,
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train as yolo_train_service

    launched = []

    def fake_submit(job_id, task_id, cmd, *, resume=False):
        yolo_train_service._ACTIVE_TRAIN_JOBS.add(int(job_id))
        launched.append((job_id, task_id, cmd, resume))

    monkeypatch.setattr(
        yolo_train_service, "ensure_ultralytics_installed", lambda reason=None: True
    )
    monkeypatch.setattr(yolo_train_service, "_submit_yolo_train_job", fake_submit)

    work = media_root / settings.YOLO_TRAIN_WORK_SUBDIR / "job-89"
    last_pt = work / "train" / "weights" / "last.pt"
    last_pt.parent.mkdir(parents=True, exist_ok=True)
    last_pt.write_bytes(b"fake-last-pt")

    with db.begin():
        job = YoloTrainJob(
            id=89,
            task_id=finished_task,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11s.pt",
            epochs=12,
            imgsz=704,
        )
        db.add(job)

    yolo_train_service._ACTIVE_TRAIN_JOBS.add(89)

    r = client.post(
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/89/restart",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    assert r.json()["data"]["runtime_status"] == "RUNNING"
    assert len(launched) == 1
    assert launched[0][0] == 89
    assert launched[0][3] is True


def test_completed_job_restart_is_rejected(
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
        f"/api/v1/tasks/{finished_task}/yolo-train-jobs/{job_id}/restart",
        headers=testuser_token_headers,
    )

    assert r.status_code == 400
