from __future__ import annotations

import json

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus
from labelu.internal.application.command.yolo_quality_policy import CreateOrUpdateYoloQualityPolicyCommand
from labelu.internal.application.service import yolo_quality_policy as yolo_quality_policy_service


def test_count_training_lineage_jobs(db):
    from labelu.internal.application.service.yolo_train_auto_iteration import (
        count_training_lineage_jobs,
    )

    with db.begin():
        task = Task(
            name="pytest-lineage",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        j1 = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
        )
        db.add(j1)
        db.flush()
        j2 = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights=f"trained:{j1.id}",
        )
        db.add(j2)
        db.flush()
        j3 = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights=f"trained:{j2.id}",
        )
        db.add(j3)
        db.flush()
        jid3 = j3.id

    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == jid3).one()
    assert count_training_lineage_jobs(db, row) == 3


def test_pick_best_next_iteration_report_prefers_actionable(db):
    from labelu.internal.application.service.yolo_train_auto_iteration import (
        pick_best_next_iteration_report,
    )

    rows = [
        YoloEvalReport(
            id=1,
            yolo_train_job_id=10,
            provider_key="volcengine",
            report_kind="next_iteration",
            suggested_params=json.dumps({"epochs": 80, "lr0": 0.005}),
        ),
        YoloEvalReport(
            id=2,
            yolo_train_job_id=10,
            provider_key="qwen_plus",
            report_kind="next_iteration",
            suggested_params=json.dumps({"notes": "only"}),
        ),
    ]
    pick = pick_best_next_iteration_report(rows)
    assert pick is not None
    assert pick.provider_key == "volcengine"


def test_pick_best_next_iteration_report_ignores_retired_qwen():
    from labelu.internal.application.service.yolo_train_auto_iteration import (
        pick_best_next_iteration_report,
    )

    row = YoloEvalReport(
        id=1, yolo_train_job_id=10, provider_key="qwen_plus",
        report_kind="next_iteration",
        suggested_params=json.dumps({"epochs": 80, "lr0": 0.005}),
    )
    assert pick_best_next_iteration_report([row]) is None


def test_maybe_start_auto_iteration_skips_when_passed(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_auto_iteration

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_ITERATION_ENABLED", True)

    weights = (
        tmp_path
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / "job-201"
        / "train"
        / "weights"
        / "best.pt"
    )
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"fake")
    metrics = json.dumps(
        {
            "final_metrics": {
                "map50_95": 0.9,
                "map50": 0.92,
                "epoch": 50,
            }
        }
    )

    with db.begin():
        task = Task(
            name="pytest-auto-iter-pass",
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
            id=201,
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
            metrics_summary=metrics,
            ai_eval_next_status="COMPLETED",
        )
        db.add(job)

    submit_calls: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._submit_yolo_train_job",
        lambda jid, tid, cmd: submit_calls.append(jid),
    )

    yolo_train_auto_iteration.maybe_start_auto_iteration_train(201)
    assert submit_calls == []


def test_legacy_auto_iteration_hard_skips_new_auto_training_plan_job(
    db, monkeypatch
):
    from labelu.internal.application.service import yolo_train_auto_iteration

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_ITERATION_ENABLED", True)
    with db.begin():
        task = Task(
            name="pytest-new-auto-plan-skip",
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
                id=299,
                task_id=task.id,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
                model_weights="yolo11n.pt",
                ai_eval_next_status="COMPLETED",
                auto_training_plan_id=77,
                auto_training_round_id=88,
            )
        )

    monkeypatch.setattr(
        yolo_train_auto_iteration,
        "_job_is_validation_passed",
        lambda _job: (_ for _ in ()).throw(AssertionError("legacy path must not run")),
    )
    yolo_train_auto_iteration.maybe_start_auto_iteration_train(299)


def test_maybe_start_auto_iteration_uses_active_quality_policy(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_auto_iteration

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_ITERATION_ENABLED", True)
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="strict-scene",
            scene_name="strict-scene",
            rect_preset_slug="strict_scene",
            status="active",
            source="manual",
            training_quality={"map50_95_pass": 0.95, "map50_warn": 0.90},
        )
    )

    weights = (
        tmp_path
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / "job-202"
        / "train"
        / "weights"
        / "best.pt"
    )
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"fake")
    metrics = json.dumps(
        {
            "final_metrics": {
                "map50_95": 0.9,
                "map50": 0.92,
                "epoch": 50,
            },
            "dataset": {"train": 100, "val": 20, "test": 0},
        }
    )

    with db.begin():
        task = Task(
            name="pytest-auto-iter-policy",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "strict_scene"}, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            id=202,
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
            metrics_summary=metrics,
            ai_eval_next_status="PENDING",
        )
        db.add(job)

    submit_calls: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._submit_yolo_train_job",
        lambda jid, tid, cmd: submit_calls.append(jid),
    )

    yolo_train_auto_iteration.maybe_start_auto_iteration_train(202)
    assert submit_calls == []


def test_submit_yolo_train_job_from_worker_thread_starts_sync(monkeypatch):
    """评估线程内自动续训：无 running loop 时应 daemon Thread 调 _run_yolo_train_sync。"""
    import threading

    from labelu.internal.application.service import yolo_train as train_svc
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand

    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.setattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", False)
    monkeypatch.setattr(yolo_train_queue, "_resolve_train_start_slot", lambda *_args: (True, "", "cpu"))
    monkeypatch.setattr(yolo_train_queue, "_queue_metadata", lambda _job_id: ("USER", 0))
    monkeypatch.setattr(yolo_train_queue, "dispatch_all_available_train_jobs", lambda: 0)

    calls: list[int] = []
    worker_done = threading.Event()

    def _record_start(job_id, *_args, **_kwargs):
        calls.append(int(job_id))
        worker_done.set()

    monkeypatch.setattr(
        yolo_train_queue,
        "_run_yolo_train_subprocess",
        _record_start,
    )

    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt")
    done = threading.Event()

    def _from_thread():
        train_svc._submit_yolo_train_job(68, 1, cmd)
        done.set()

    threading.Thread(target=_from_thread).start()
    assert done.wait(5)
    assert worker_done.wait(5)
    assert calls == [68]
    # The queue owns the active marker and clears it when the worker exits.
    assert 68 not in train_svc._ACTIVE_TRAIN_JOBS


def test_merge_ai_suggested_into_iteration_params_includes_epochs_imgsz():
    from labelu.internal.common.yolo_train_hyperparams import (
        merge_ai_suggested_into_hyperparams,
        merge_ai_suggested_into_iteration_params,
    )

    payload = json.dumps(
        {
            "recommended_train_params": {
                "epochs": 100,
                "imgsz": 1280,
                "lr0": 0.0008,
                "optimizer": "AdamW",
                "mosaic": 0.7,
                "notes": "ignored",
            }
        }
    )
    hp_only = merge_ai_suggested_into_hyperparams(payload)
    assert "epochs" not in hp_only
    assert "imgsz" not in hp_only
    assert hp_only["lr0"] == 0.0008

    full = merge_ai_suggested_into_iteration_params(payload)
    assert full["epochs"] == 100
    assert full["imgsz"] == 1280
    assert full["lr0"] == 0.0008
    assert full["optimizer"] == "AdamW"


def test_build_followup_command_applies_ai_epochs_and_imgsz(db):
    from labelu.internal.application.service.yolo_train_auto_iteration import (
        _build_followup_command,
    )

    suggested = json.dumps(
        {
            "recommended_train_params": {
                "epochs": 100,
                "imgsz": 1280,
                "lr0": 0.001,
                "optimizer": "AdamW",
                "mosaic": 0.5,
                "patience": 10,
            }
        }
    )
    with db.begin():
        task = Task(
            name="pytest-followup-cmd",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        parent = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            epochs=50,
            imgsz=640,
        )
        db.add(parent)
        db.flush()
        parent_id = parent.id
        report = YoloEvalReport(
            yolo_train_job_id=parent_id,
            provider_key="qwen_plus",
            report_kind="next_iteration",
            suggested_params=suggested,
        )

    parent_row = db.query(YoloTrainJob).filter(YoloTrainJob.id == parent_id).one()
    cmd = _build_followup_command(db, completed_job=parent_row, report=report)
    assert cmd is not None
    assert cmd.epochs == 100
    assert cmd.imgsz == 1280
    assert cmd.weights_source == "trained"
    assert cmd.weights_ref_id == parent_id
    assert cmd.hyperparams["lr0"] == 0.001
    assert cmd.hyperparams["mosaic"] == 0.5
    assert cmd.hyperparams["patience"] == 30


def test_build_followup_command_merges_full_and_next_reports(db):
    from labelu.internal.application.service.yolo_train_auto_iteration import (
        _build_followup_command,
    )

    full_suggested = json.dumps(
        {
            "recommended_train_params": {
                "cls": 0.5,
                "mosaic": 0.3,
                "copy_paste": 0.0,
            }
        }
    )
    next_suggested = json.dumps(
        {
            "recommended_train_params": {
                "epochs": 200,
                "imgsz": 1280,
                "batch": 16,
                "mosaic": 0.7,
                "optimizer": "AdamW",
            }
        }
    )
    with db.begin():
        task = Task(
            name="pytest-merge-reports",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        parent = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            epochs=50,
            imgsz=640,
        )
        db.add(parent)
        db.flush()
        parent_id = parent.id
        full_report = YoloEvalReport(
            yolo_train_job_id=parent_id,
            provider_key="volcengine",
            report_kind="full_analysis",
            suggested_params=full_suggested,
        )
        next_report = YoloEvalReport(
            yolo_train_job_id=parent_id,
            provider_key="volcengine",
            report_kind="next_iteration",
            suggested_params=next_suggested,
        )

    parent_row = db.query(YoloTrainJob).filter(YoloTrainJob.id == parent_id).one()
    cmd = _build_followup_command(
        db,
        completed_job=parent_row,
        report=next_report,
        full_report=full_report,
    )
    assert cmd is not None
    assert cmd.epochs == 200
    assert cmd.imgsz == 1280
    assert cmd.hyperparams["mosaic"] == 0.7
    assert cmd.hyperparams["cls"] == 0.5
    assert cmd.hyperparams["copy_paste"] == 0.0
    assert cmd.hyperparams["optimizer"] == "AdamW"
