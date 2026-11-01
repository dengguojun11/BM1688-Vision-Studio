"""自动 BM1688 编译的质量门禁测试（达标才编译）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from labelu.internal.common.auto_iteration_runtime import (
    invalidate_auto_iteration_cache,
)
from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    clear_runtime_override,
    set_runtime_override,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture(autouse=True)
def _compile_policy():
    clear_runtime_override("auto_iteration")
    invalidate_auto_iteration_cache()
    for key, value in (
        ("enabled", True),
        ("metric_basis", "val_recall"),
        ("pass_threshold", 0.6),
        ("warn_threshold", 0.5),
        ("require_quality_for_compile", True),
    ):
        set_runtime_override("auto_iteration", key, value)
    invalidate_auto_iteration_cache()
    yield
    clear_runtime_override("auto_iteration")
    invalidate_auto_iteration_cache()


def _metrics(recall: float) -> str:
    return json.dumps(
        {
            "final_metrics": {
                "map50_95": 0.3,
                "map50": 0.4,
                "precision": 0.8,
                "recall": recall,
                "epoch": 50,
            },
            "dataset": {"train": 100, "val": 20},
        }
    )


def _seed_job(db, tmp_path, *, recall: float) -> int:
    weights = tmp_path / "job" / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"fake")
    with db.begin():
        task = Task(
            name="pytest-compile-gate",
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
            task_id=int(task.id),
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="yolo11n.pt",
            weights_path=str(weights),
            metrics_summary=_metrics(recall),
        )
        db.add(job)
        db.flush()
        return int(job.id)


def _patch_export_pipeline(monkeypatch, submitted: list[int]):
    from labelu.internal.application.service import (
        yolo_bm1688_auto_compile as auto_compile,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED", True)
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(
        auto_compile.crud_task,
        "get",
        lambda db, task_id: SimpleNamespace(id=task_id, config="{}"),
    )
    monkeypatch.setattr(
        auto_compile.crud_user, "get", lambda db, id: SimpleNamespace(id=id)
    )
    monkeypatch.setattr(
        auto_compile.crud_yolo_bm1688_export,
        "list_all_for_job",
        lambda db, yolo_train_job_id: [],
    )
    monkeypatch.setattr(
        auto_compile.export_service,
        "create_export_for_job_system",
        lambda db, **kwargs: (SimpleNamespace(id=99), True),
    )
    monkeypatch.setattr(
        auto_compile.export_service,
        "submit_export_background",
        lambda export_id: submitted.append(int(export_id)),
    )
    return auto_compile


def test_auto_compile_skips_when_quality_below_threshold(db, tmp_path, monkeypatch):
    submitted: list[int] = []
    auto_compile = _patch_export_pipeline(monkeypatch, submitted)
    job_id = _seed_job(db, tmp_path, recall=0.4)

    assert auto_compile.maybe_auto_compile_job(job_id) is False
    assert submitted == []


def test_auto_compile_runs_when_quality_passed(db, tmp_path, monkeypatch):
    submitted: list[int] = []
    auto_compile = _patch_export_pipeline(monkeypatch, submitted)
    job_id = _seed_job(db, tmp_path, recall=0.8)

    assert auto_compile.maybe_auto_compile_job(job_id) is True
    assert submitted == [99]


def test_auto_compile_gate_can_be_disabled(db, tmp_path, monkeypatch):
    submitted: list[int] = []
    auto_compile = _patch_export_pipeline(monkeypatch, submitted)
    set_runtime_override("auto_iteration", "require_quality_for_compile", False)
    invalidate_auto_iteration_cache()
    job_id = _seed_job(db, tmp_path, recall=0.1)

    assert auto_compile.maybe_auto_compile_job(job_id) is True
    assert submitted == [99]
