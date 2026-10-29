from __future__ import annotations

import json

import pytest

from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_resolve_eval_weights_keeps_standard_yolo_path(tmp_path):
    from labelu.internal.application.service import yolo_train_evaluation

    weights = tmp_path / "job-100" / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"fake")
    fallback = (
        tmp_path
        / "job-100"
        / "plate_detection"
        / "pose_train"
        / "train"
        / "weights"
        / "best.pt"
    )
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"fallback")

    job = YoloTrainJob(
        weights_path=str(weights),
        component_artifacts_json=json.dumps(
            {"pose": {"evaluation_weights": str(fallback)}}
        ),
    )

    resolved, train_dir = yolo_train_evaluation._resolve_eval_weights(job)

    assert resolved == weights.resolve()
    assert train_dir == weights.parent.parent.resolve()


def test_resolve_eval_weights_uses_component_checkpoint_for_plate_job(tmp_path):
    from labelu.internal.application.service import yolo_train_evaluation

    deployable = tmp_path / "job-301" / "plate_detection" / "plate_detection_best.pt"
    deployable.parent.mkdir(parents=True)
    deployable.write_bytes(b"deployable")
    evaluation = (
        tmp_path
        / "job-301"
        / "plate_detection"
        / "pose_train"
        / "train"
        / "weights"
        / "best.pt"
    )
    evaluation.parent.mkdir(parents=True)
    evaluation.write_bytes(b"evaluation")

    job = YoloTrainJob(
        weights_path=str(deployable),
        component_artifacts_json=json.dumps(
            {"pose": {"pt": str(deployable), "evaluation_weights": str(evaluation)}}
        ),
    )

    resolved, train_dir = yolo_train_evaluation._resolve_eval_weights(job)

    assert resolved == evaluation.resolve()
    assert train_dir == evaluation.parent.parent.resolve()


def test_run_eval_uses_plate_component_checkpoint(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_evaluation

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    deployable = tmp_path / "job-104" / "plate_detection" / "plate_detection_best.pt"
    deployable.parent.mkdir(parents=True)
    deployable.write_bytes(b"deployable")
    evaluation = (
        tmp_path
        / "job-104"
        / "plate_detection"
        / "pose_train"
        / "train"
        / "weights"
        / "best.pt"
    )
    evaluation.parent.mkdir(parents=True)
    evaluation.write_bytes(b"evaluation")
    train_dir = evaluation.parent.parent
    (train_dir / "args.yaml").write_text("epochs: 1\n", encoding="utf-8")
    (train_dir / "results.csv").write_text(
        "epoch,metrics/mAP50(B)\n0,0.9\n", encoding="utf-8"
    )

    with db.begin():
        task = Task(
            name="pytest-plate-auto-eval",
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
                id=104,
                task_id=task.id,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
                job_type="plate_detection",
                model_weights="yolov8n-pose.pt",
                weights_path=str(deployable),
                component_artifacts_json=json.dumps(
                    {
                        "pose": {
                            "pt": str(deployable),
                            "evaluation_weights": str(evaluation),
                        }
                    }
                ),
                metrics_summary=json.dumps({"final_metrics": {"map50": 0.9}}),
            )
        )

    captured = {}
    monkeypatch.setattr(
        yolo_train_evaluation,
        "_build_provider_specs",
        lambda: [("mock", "http://mock", "key", "model")],
    )
    monkeypatch.setattr(
        yolo_train_evaluation,
        "load_yolo_eval_prompts",
        lambda: {
            "full_analysis": "weights={{WEIGHTS_PATH}}",
            "next_iteration": "weights={{WEIGHTS_PATH}}",
        },
    )

    def fake_chat(**kwargs):
        captured["user_text"] = kwargs["user_text"]
        return "# report"

    monkeypatch.setattr(yolo_train_evaluation, "chat_multimodal_markdown", fake_chat)

    yolo_train_evaluation._run_yolo_eval_sync(104, "full_analysis")

    db.expire_all()
    report = (
        db.query(YoloEvalReport).filter(YoloEvalReport.yolo_train_job_id == 104).one()
    )
    job = db.query(YoloTrainJob).filter(YoloTrainJob.id == 104).one()
    assert report.body == "# report"
    assert report.error_message is None
    assert job.ai_eval_full_status == "COMPLETED"
    assert str(evaluation.resolve()) in captured["user_text"]


@pytest.mark.parametrize(
    "outcome,expected_status,expected_reports,expected_iteration_calls",
    [
        ("unconfigured", "FAILED", 0, 0),
        ("exception", "FAILED", 1, 0),
        ("empty", "FAILED", 1, 0),
        ("success", "COMPLETED", 1, 1),
        ("partial_success", "COMPLETED", 2, 1),
    ],
)
def test_run_eval_requires_successful_report_before_auto_iteration(
    db,
    tmp_path,
    monkeypatch,
    outcome,
    expected_status,
    expected_reports,
    expected_iteration_calls,
):
    from labelu.internal.application.service import yolo_train_evaluation as service
    from labelu.internal.application.service import (
        yolo_train_auto_iteration as iteration,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    weights = tmp_path / "job-test" / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"test-checkpoint")
    with db.begin():
        task = Task(
            name="pytest-eval-result",
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
            weights_path=str(weights),
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    providers = (
        []
        if outcome == "unconfigured"
        else [("mock", "http://mock", "test-key", "test-model")]
    )
    if outcome == "partial_success":
        providers.append(
            ("mock-success", "http://mock-success", "test-key", "test-model")
        )
    monkeypatch.setattr(service, "_build_provider_specs", lambda: providers)

    def fake_chat(**kwargs):
        if outcome == "exception" or (
            outcome == "partial_success" and kwargs["base_url"] == "http://mock"
        ):
            raise RuntimeError("upstream unavailable")
        return " \n" if outcome == "empty" else "# 有效评估报告"

    monkeypatch.setattr(service, "chat_multimodal_markdown", fake_chat)
    iteration_calls = []
    monkeypatch.setattr(
        iteration, "maybe_start_auto_iteration_train", iteration_calls.append
    )

    service._run_yolo_eval_sync(job_id, "next_iteration")

    db.expire_all()
    stored_job = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    reports = (
        db.query(YoloEvalReport)
        .filter(YoloEvalReport.yolo_train_job_id == job_id)
        .all()
    )
    assert stored_job.ai_eval_next_status == expected_status
    assert len(reports) == expected_reports
    assert len(iteration_calls) == expected_iteration_calls
    if outcome in {"empty", "exception"}:
        assert reports[0].body is None
        assert reports[0].error_message


def test_auto_eval_retries_failed_generation_and_preserves_history(
    db, tmp_path, monkeypatch
):
    from labelu.internal.adapter.persistence import (
        crud_yolo_eval_report,
        crud_yolo_train_job,
    )
    from labelu.internal.application.service import yolo_train_evaluation as service

    monkeypatch.setattr(settings, "YOLO_EVAL_ENABLED", True)
    monkeypatch.setattr(
        service,
        "_build_provider_specs",
        lambda: [("mock", "http://mock", "test-key", "test-model")],
    )
    with db.begin():
        task = Task(
            name="pytest-failed-eval-recovery",
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
            weights_path=str(tmp_path / "best.pt"),
            model_weights="yolo11n.pt",
            ai_eval_full_status="FAILED",
            ai_eval_next_status="FAILED",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        for kind in ("full_analysis", "next_iteration"):
            db.add(
                YoloEvalReport(
                    yolo_train_job_id=job_id,
                    provider_key="mock",
                    report_kind=kind,
                    body=None,
                    error_message="upstream unavailable",
                )
            )

    completed_operations = []

    def complete_report(job_id, kind, operation_id):
        completed_operations.append((kind, operation_id))
        with db.begin():
            crud_yolo_eval_report.create(
                db,
                job_id=job_id,
                provider_key="mock",
                report_kind=kind,
                operation_id=operation_id,
                body="# 新报告",
                suggested_params=None,
                error_message=None,
            )
        with db.begin():
            crud_yolo_train_job.update_eval_status_for_operation(
                db,
                job_id=job_id,
                report_kind=kind,
                operation_id=operation_id,
                value="COMPLETED",
            )

    monkeypatch.setattr(service, "_run_yolo_eval_for_operation", complete_report)
    service.run_auto_evaluations_after_train(job_id)

    db.expire_all()
    stored_job = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    reports = (
        db.query(YoloEvalReport)
        .filter(YoloEvalReport.yolo_train_job_id == job_id)
        .all()
    )
    assert stored_job.ai_eval_full_status == "COMPLETED"
    assert stored_job.ai_eval_next_status == "COMPLETED"
    assert len(completed_operations) == 2
    assert len(reports) == 4
    assert len([row for row in reports if row.error_message]) == 2


def test_auto_eval_does_not_retry_beyond_one_failed_retry(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_evaluation as service

    monkeypatch.setattr(settings, "YOLO_EVAL_ENABLED", True)
    monkeypatch.setattr(
        service,
        "_build_provider_specs",
        lambda: [("mock", "http://mock", "test-key", "test-model")],
    )
    monkeypatch.setattr(
        service,
        "_run_yolo_eval_for_operation",
        lambda *_args: pytest.fail("retry budget must be bounded"),
    )
    with db.begin():
        task = Task(
            name="pytest-failed-eval-retry-limit",
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
            weights_path=str(tmp_path / "best.pt"),
            model_weights="yolo11n.pt",
            ai_eval_full_status="FAILED",
            ai_eval_next_status="FAILED",
            ai_eval_full_operation_id="second-full",
            ai_eval_next_operation_id="second-next",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        for kind, operations in (
            ("full_analysis", ("first-full", "second-full")),
            ("next_iteration", ("first-next", "second-next")),
        ):
            for operation in operations:
                db.add(
                    YoloEvalReport(
                        yolo_train_job_id=job_id,
                        provider_key="mock",
                        report_kind=kind,
                        operation_id=operation,
                        body=None,
                        error_message="upstream unavailable",
                    )
                )

    service.run_auto_evaluations_after_train(job_id)

    db.expire_all()
    stored_job = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    assert stored_job.ai_eval_full_status == "FAILED"
    assert stored_job.ai_eval_next_status == "FAILED"


def test_auto_eval_runs_full_report_before_next_iteration(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_evaluation

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_EVAL_ENABLED", True)

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-101" / "train" / "weights"
    work.mkdir(parents=True)
    weights = work / "best.pt"
    weights.write_bytes(b"fake")

    with db.begin():
        task = Task(
            name="pytest-auto-eval",
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
            id=101,
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
        )
        db.add(job)

    calls: list[str] = []

    def fake_run(job_id: int, report_kind: str) -> None:
        calls.append(report_kind)
        yolo_train_evaluation._set_job_eval_flag(job_id, report_kind, "COMPLETED")

    monkeypatch.setattr(
        yolo_train_evaluation,
        "_build_provider_specs",
        lambda: [("mock", "http://mock", "key", "model")],
    )
    monkeypatch.setattr(yolo_train_evaluation, "_run_yolo_eval_sync", fake_run)

    yolo_train_evaluation.run_auto_evaluations_after_train(101)

    db.expire_all()
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == 101).one()
    assert calls == ["full_analysis", "next_iteration"]
    assert row.ai_eval_full_status == "COMPLETED"
    assert row.ai_eval_next_status == "COMPLETED"


def test_auto_eval_skips_without_vision_provider(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_evaluation

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_EVAL_ENABLED", True)

    weights = (
        tmp_path
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / "job-102"
        / "train"
        / "weights"
        / "best.pt"
    )
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"fake")

    with db.begin():
        task = Task(
            name="pytest-auto-eval-skip",
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
                id=102,
                task_id=task.id,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
                model_weights="yolo11n.pt",
                weights_path=str(weights),
            )
        )

    monkeypatch.setattr(yolo_train_evaluation, "_build_provider_specs", lambda: [])

    yolo_train_evaluation.run_auto_evaluations_after_train(102)

    db.expire_all()
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == 102).one()
    assert row.ai_eval_full_status is None
    assert row.ai_eval_next_status is None


def test_missing_auto_eval_recovers_orphaned_running_status(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train_evaluation

    weights = tmp_path / "job-103" / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"fake")

    with db.begin():
        task = Task(
            name="pytest-auto-eval-recover",
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
            id=103,
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
            ai_eval_full_status="RUNNING",
        )
        db.add(job)

    scheduled: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        yolo_train_evaluation,
        "_build_provider_specs",
        lambda: [("mock", "http://mock", "key", "model")],
    )
    monkeypatch.setattr(
        yolo_train_evaluation,
        "schedule_auto_evaluations_after_train",
        lambda job_id, recover_interrupted=False: scheduled.append(
            (job_id, recover_interrupted)
        )
        or True,
    )

    assert yolo_train_evaluation._schedule_missing_auto_evaluations(db, job) is True
    assert scheduled == [(103, True)]


def test_eval_status_claim_is_compare_and_set(db):
    from labelu.internal.adapter.persistence import crud_yolo_train_job

    with db.begin():
        task = Task(
            name="pytest-eval-claim",
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
                id=105,
                task_id=task.id,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
                weights_path="/tmp/best.pt",
            )
        )

    with db.begin():
        assert crud_yolo_train_job.update_eval_status_if_current(
            db,
            job_id=105,
            report_kind="full_analysis",
            expected=None,
            value="RUNNING",
        )
    with db.begin():
        assert not crud_yolo_train_job.update_eval_status_if_current(
            db,
            job_id=105,
            report_kind="full_analysis",
            expected=None,
            value="RUNNING",
        )
        assert crud_yolo_train_job.update_eval_status_if_current(
            db,
            job_id=105,
            report_kind="full_analysis",
            expected="RUNNING",
            value="COMPLETED",
        )


def test_eval_generation_fences_late_worker_and_report_retry(db):
    from labelu.internal.adapter.persistence import (
        crud_yolo_eval_report,
        crud_yolo_train_job,
    )

    with db.begin():
        task = Task(
            name="pytest-eval-generation-fence",
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
                id=106,
                task_id=task.id,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
                weights_path="/tmp/best.pt",
            )
        )

    with db.begin():
        first = crud_yolo_train_job.claim_eval_status_if_current(
            db,
            job_id=106,
            report_kind="full_analysis",
            expected=None,
        )
    assert first
    db.rollback()

    with db.begin():
        second = crud_yolo_train_job.claim_eval_status_if_current(
            db,
            job_id=106,
            report_kind="full_analysis",
            expected="RUNNING",
        )
    assert second and second != first
    db.rollback()

    with db.begin():
        assert not crud_yolo_train_job.update_eval_status_for_operation(
            db,
            job_id=106,
            report_kind="full_analysis",
            operation_id=first,
            value="COMPLETED",
        )
        accepted = crud_yolo_eval_report.create(
            db,
            job_id=106,
            provider_key="mock",
            report_kind="full_analysis",
            operation_id=first,
            body="late",
            suggested_params=None,
            error_message=None,
        )
    assert accepted is None
    db.rollback()

    with db.begin():
        report = crud_yolo_eval_report.create(
            db,
            job_id=106,
            provider_key="mock",
            report_kind="full_analysis",
            operation_id=second,
            body="current",
            suggested_params=None,
            error_message=None,
        )
        retry = crud_yolo_eval_report.create(
            db,
            job_id=106,
            provider_key="mock",
            report_kind="full_analysis",
            operation_id=second,
            body="retry should be idempotent",
            suggested_params=None,
            error_message=None,
        )
    assert report is not None
    assert retry is not None
    assert retry.id == report.id
    db.rollback()

    with db.begin():
        assert crud_yolo_train_job.update_eval_status_for_operation(
            db,
            job_id=106,
            report_kind="full_analysis",
            operation_id=second,
            value="COMPLETED",
        )
