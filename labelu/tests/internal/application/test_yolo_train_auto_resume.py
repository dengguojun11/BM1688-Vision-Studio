from __future__ import annotations

import json
import signal

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_user_pause_blocks_auto_resume(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_auto_resume import (
        maybe_auto_resume_train_job,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-301" / "train" / "weights"
    work.mkdir(parents=True)
    (work / "last.pt").write_bytes(b"fake")

    with db.begin():
        task = Task(
            name="pytest-auto-resume-block",
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
            id=301,
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)

    yolo_train.mark_user_pause_requested(301)
    submit_calls: list[tuple] = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda jid, tid, cmd, resume=False: submit_calls.append((jid, resume)),
    )

    assert maybe_auto_resume_train_job(301) is False
    assert submit_calls == []


def test_auto_resume_cancelled_without_user_pause(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_auto_resume import (
        maybe_auto_resume_train_job,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        yolo_train,
        "ensure_ultralytics_installed",
        lambda reason="": True,
    )

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-302" / "train" / "weights"
    work.mkdir(parents=True)
    (work / "last.pt").write_bytes(b"fake")

    with db.begin():
        task = Task(
            name="pytest-auto-resume-ok",
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
            id=302,
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)

    submit_calls: list[tuple] = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda jid, tid, cmd, resume=False: submit_calls.append((jid, resume)),
    )

    assert maybe_auto_resume_train_job(302) is True
    assert submit_calls == [(302, True)]
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == 302).one()
    assert row.status == YoloTrainStatus.PROCESSING.value


def test_phase_one_gate_skips_automatic_resume_but_keeps_manual_resume(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_auto_resume import (
        maybe_auto_resume_train_job,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(
        yolo_train, "ensure_ultralytics_installed", lambda reason="": True
    )

    with db.begin():
        task = Task(
            name="pytest-phase-one-resume-gate",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        automatic = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11n.pt",
            queue_source="AUTO",
        )
        manual = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.CANCELLED.value,
            model_weights="yolo11n.pt",
            queue_source="MANUAL",
        )
        db.add_all([automatic, manual])
        db.flush()
        automatic_id = int(automatic.id)
        manual_id = int(manual.id)

    for job_id in (automatic_id, manual_id):
        weights = (
            tmp_path
            / settings.YOLO_TRAIN_WORK_SUBDIR
            / f"job-{job_id}"
            / "train"
            / "weights"
        )
        weights.mkdir(parents=True)
        (weights / "last.pt").write_bytes(b"fake")

    submit_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda jid, tid, cmd, resume=False: submit_calls.append((int(jid), resume)),
    )

    assert maybe_auto_resume_train_job(automatic_id) is False
    assert maybe_auto_resume_train_job(manual_id) is True
    assert submit_calls == [(manual_id, True)]


def test_pause_api_sets_user_pause_flag(
    client, testuser_token_headers, db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)

    with db.begin():
        task = Task(
            name="pytest-pause-flag",
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
        )
        db.add(job)
        db.flush()
        job_id = job.id
        task_id = task.id

    r = client.post(
        f"/api/v1/tasks/{task_id}/yolo-train-jobs/{job_id}/pause",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert yolo_train.is_user_pause_requested(job_id)


def test_pause_api_terminates_runtime_process_tree(
    client, testuser_token_headers, db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)

    with db.begin():
        task = Task(
            name="pytest-pause-kill-ddp",
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
        )
        db.add(job)
        db.flush()
        job_id = job.id
        task_id = task.id

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 300}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        yolo_train, "_linux_process_tree_pids", lambda pid: {300, 301, 302}
    )
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        yolo_train.os, "kill", lambda pid, sig: killed.append((pid, sig))
    )

    r = client.post(
        f"/api/v1/tasks/{task_id}/yolo-train-jobs/{job_id}/pause",
        headers=testuser_token_headers,
    )

    assert r.status_code == 200
    assert killed == [
        (302, signal.SIGTERM),
        (301, signal.SIGTERM),
        (300, signal.SIGTERM),
    ]
