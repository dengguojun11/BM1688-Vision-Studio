from __future__ import annotations

import json
import threading

from labelu.internal.common.config import settings
from labelu.internal.common.yolo_train_deploy import (
    _read_deploy_train_phase,
    clear_deploy_pause_in_progress,
    clear_deploy_resume_queue,
    deploy_pause_lock_path,
    deploy_resume_claim_lock,
    deploy_resume_claim_lock_path,
    deploy_resume_queue_path,
    is_deploy_pause_in_progress,
    job_is_active_for_deploy,
    load_deploy_resume_queue,
    pause_active_jobs_for_deploy,
    pause_train_job_for_deploy,
    resume_deploy_paused_jobs,
    save_deploy_resume_queue,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_deploy_resume_claim_lock_rejects_parallel_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    competing_result: list[bool] = []

    def _compete() -> None:
        with deploy_resume_claim_lock() as acquired_inner:
            competing_result.append(acquired_inner)

    with deploy_resume_claim_lock() as acquired:
        assert acquired is True
        thread = threading.Thread(target=_compete)
        thread.start()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert competing_result == [False]
    assert deploy_resume_claim_lock_path().is_file()


def test_deploy_pause_does_not_set_user_pause(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)

    with db.begin():
        task = Task(
            name="pytest-deploy-pause",
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

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 99999}),
        encoding="utf-8",
    )

    monkeypatch.setattr(yolo_train, "_linux_process_tree_pids", lambda pid: set())
    monkeypatch.setattr(yolo_train, "_runtime_train_process_on_gpu", lambda pid: False)

    assert pause_train_job_for_deploy(db, job_id) is True
    assert not yolo_train.is_user_pause_requested(job_id)
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    assert row.status == YoloTrainStatus.CANCELLED.value
    assert "部署发版自动暂停" in (row.error_message or "")


def test_deploy_pause_and_resume_flow(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        yolo_train,
        "ensure_ultralytics_installed",
        lambda reason="": True,
    )
    monkeypatch.setattr(yolo_train, "_linux_process_tree_pids", lambda pid: set())
    monkeypatch.setattr(yolo_train, "_runtime_train_process_on_gpu", lambda pid: False)

    with db.begin():
        task = Task(
            name="pytest-deploy-flow",
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

    weights = (
        tmp_path
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "train"
        / "weights"
    )
    weights.mkdir(parents=True)
    (weights / "last.pt").write_bytes(b"fake")
    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    (work / "phase.json").write_text(
        json.dumps({"phase": "running"}),
        encoding="utf-8",
    )

    submit_calls: list[tuple] = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda jid, tid, cmd, resume=False: submit_calls.append((jid, resume)),
    )

    paused = pause_active_jobs_for_deploy(db, wait_timeout_seconds=30)
    assert paused == [job_id]
    assert is_deploy_pause_in_progress()
    assert load_deploy_resume_queue() == [job_id]

    from labelu.internal.application.service.yolo_train_auto_resume import (
        maybe_auto_resume_train_job,
    )

    assert maybe_auto_resume_train_job(job_id) is False

    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_deploy.time.sleep",
        lambda _s: None,
    )
    n = resume_deploy_paused_jobs(delay_seconds=0)
    assert n == 1
    assert submit_calls == [(job_id, True)]
    assert load_deploy_resume_queue() == []
    assert not is_deploy_pause_in_progress()


def test_job_is_active_for_deploy_ignores_cancelled_phase(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))

    with db.begin():
        task = Task(
            name="pytest-deploy-inactive",
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

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps({"phase": "cancelled"}),
        encoding="utf-8",
    )

    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    assert job_is_active_for_deploy(row) is False


def test_read_deploy_train_phase_ignores_extra_fields(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._read_train_phase",
        lambda job_id: ("running", "训练中", 4321, "detail"),
    )

    phase, runtime_pid = _read_deploy_train_phase(7)

    assert phase == "running"
    assert runtime_pid == 4321


def test_save_and_load_deploy_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    clear_deploy_pause_in_progress()
    clear_deploy_resume_queue()
    save_deploy_resume_queue([3, 1, 3])
    assert load_deploy_resume_queue() == [1, 3]
    assert deploy_resume_queue_path().is_file()
    clear_deploy_resume_queue()
    assert load_deploy_resume_queue() == []


def test_deploy_lock_path(tmp_path, monkeypatch):
    from labelu.internal.common.yolo_train_deploy import mark_deploy_pause_in_progress

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    clear_deploy_pause_in_progress()
    assert not deploy_pause_lock_path().is_file()
    mark_deploy_pause_in_progress()
    assert is_deploy_pause_in_progress()
    clear_deploy_pause_in_progress()


def test_deploy_resume_cli_fails_when_queue_remains(monkeypatch, capsys):
    from labelu.scripts import yolo_train_deploy

    monkeypatch.setattr(
        "sys.argv",
        ["yolo_train_deploy", "resume"],
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_deploy.deploy_resume_cli",
        lambda: {"resumed": 1, "queue_remaining": [119]},
    )

    assert yolo_train_deploy.main() == 1
    captured = capsys.readouterr()
    assert '"queue_remaining": [119]' in captured.out
    assert "仍有 1 个作业未恢复" in captured.err
