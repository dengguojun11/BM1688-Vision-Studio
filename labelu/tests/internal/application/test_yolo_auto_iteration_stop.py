"""自动迭代链：停止原因、轮次血缘与无提升刹车的集成测试。"""

from __future__ import annotations

import json

import pytest

from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common.auto_iteration_runtime import (
    invalidate_auto_iteration_cache,
)
from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    clear_runtime_override,
    set_runtime_override,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture(autouse=True)
def _iteration_policy():
    clear_runtime_override("auto_iteration")
    invalidate_auto_iteration_cache()
    for key, value in (
        ("enabled", True),
        ("metric_basis", "val_recall"),
        ("pass_threshold", 0.6),
        ("warn_threshold", 0.5),
        ("max_rounds", 4),
        ("no_improvement_rounds", 2),
        ("min_improvement_delta", 0.02),
    ):
        set_runtime_override("auto_iteration", key, value)
    invalidate_auto_iteration_cache()
    yield
    clear_runtime_override("auto_iteration")
    invalidate_auto_iteration_cache()


def _metrics(recall: float | None) -> str:
    final = {"map50_95": 0.3, "map50": 0.4, "precision": 0.8, "epoch": 50}
    if recall is not None:
        final["recall"] = recall
    return json.dumps({"final_metrics": final, "dataset": {"train": 100, "val": 20}})


def _weights_path(tmp_path, job_id: int) -> str:
    path = (
        tmp_path
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "train"
        / "weights"
        / "best.pt"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    return str(path)


def _task(db) -> int:
    with db.begin():
        task = Task(
            name="pytest-iteration-chain",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        return int(task.id)


def _job(
    db, task_id: int, *, recall, parent_id=None, next_status="COMPLETED", tmp_path=None
):
    job = YoloTrainJob(
        task_id=task_id,
        created_by=1,
        status=YoloTrainStatus.COMPLETED.value,
        model_weights=f"trained:{parent_id}" if parent_id else "yolo11n.pt",
        weights_path=_weights_path(tmp_path, 9000 + (parent_id or 0)),
        metrics_summary=_metrics(recall),
        ai_eval_next_status=next_status,
    )
    with db.begin():
        db.add(job)
        db.flush()
        return int(job.id)


def test_round_limit_writes_stop_reason(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_auto_iteration as iteration,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    task_id = _task(db)
    job_ids = []
    parent = None
    for _ in range(4):
        parent = _job(
            db,
            task_id,
            recall=0.4,
            parent_id=parent,
            tmp_path=tmp_path,
        )
        job_ids.append(parent)

    iteration.maybe_start_auto_iteration_train(job_ids[-1])

    db.expire_all()
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_ids[-1]).one()
    assert row.auto_iteration_stop_reason == "ROUND_LIMIT"
    assert row.auto_iteration_root_id == job_ids[0]


def test_no_improvement_writes_stop_reason(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_auto_iteration as iteration,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    task_id = _task(db)
    job_ids = []
    parent = None
    for _ in range(3):
        parent = _job(db, task_id, recall=0.4, parent_id=parent, tmp_path=tmp_path)
        job_ids.append(parent)

    iteration.maybe_start_auto_iteration_train(job_ids[-1])

    db.expire_all()
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_ids[-1]).one()
    assert row.auto_iteration_stop_reason == "NO_IMPROVEMENT"


def test_missing_metrics_marks_quality_evidence_missing(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_auto_iteration as iteration,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    task_id = _task(db)
    job_id = _job(db, task_id, recall=None, tmp_path=tmp_path)

    iteration.maybe_start_auto_iteration_train(job_id)

    db.expire_all()
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    assert row.auto_iteration_stop_reason == "QUALITY_EVIDENCE_MISSING"


def test_passed_job_does_not_iterate_or_stop(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_auto_iteration as iteration,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    submitted: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._submit_yolo_train_job",
        lambda job_id, task_id, cmd: submitted.append(int(job_id)),
    )
    task_id = _task(db)
    job_id = _job(db, task_id, recall=0.72, tmp_path=tmp_path)

    iteration.maybe_start_auto_iteration_train(job_id)

    db.expire_all()
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    assert submitted == []
    assert row.auto_iteration_stop_reason is None


def test_new_round_records_lineage_and_round_no(db, tmp_path, monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_auto_iteration as iteration,
    )
    from labelu.internal.application.service import yolo_train as train_service

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    submitted: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._submit_yolo_train_job",
        lambda job_id, task_id, cmd: submitted.append(int(job_id)),
    )
    monkeypatch.setattr(
        train_service,
        "_validate_weights_source_at_create",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        iteration,
        "resolve_source_task_ids_for_create",
        lambda *_args, **_kwargs: ([], None),
    )
    monkeypatch.setattr(
        train_service,
        "_model_weights_field_for_storage",
        lambda cmd: f"trained:{cmd.weights_ref_id}",
    )

    task_id = _task(db)
    parent_id = _job(db, task_id, recall=0.4, tmp_path=tmp_path)
    with db.begin():
        db.add(
            YoloEvalReport(
                yolo_train_job_id=parent_id,
                provider_key="volcengine",
                report_kind="next_iteration",
                suggested_params=json.dumps(
                    {"recommended_train_params": {"lr0": 0.001}}
                ),
            )
        )

    def _fake_followup(db, *, completed_job, report, full_report=None):
        return CreateYoloTrainJobCommand(
            model_weights=f"trained:{completed_job.id}",
            weights_source="trained",
            weights_ref_id=int(completed_job.id),
            epochs=60,
            imgsz=640,
            hyperparams={"lr0": 0.001},
        )

    monkeypatch.setattr(iteration, "_build_followup_command", _fake_followup)

    iteration.maybe_start_auto_iteration_train(parent_id)

    db.expire_all()
    children = (
        db.query(YoloTrainJob)
        .filter(YoloTrainJob.model_weights == f"trained:{parent_id}")
        .all()
    )
    assert len(children) == 1
    child = children[0]
    assert child.auto_iteration_root_id == parent_id
    assert child.auto_iteration_round_no == 2
    assert submitted == [int(child.id)]
