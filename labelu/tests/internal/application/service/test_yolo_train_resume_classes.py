"""resume 训练结束后写 metrics 时不应因 classes 未定义而失败。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from labelu.internal.application.service.yolo_train import (
    _dataset_class_names_for_summary,
    _is_ultralytics_train_loop_complete,
)
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_dataset_class_names_from_data_yaml_on_resume(tmp_path, db):
    from labelu.internal.domain.models.task import Task

    with db.begin():
        task = Task(
            name="resume-classes",
            description="",
            tips="",
            config='{"tools":[{"tool":"rectTool","config":{"labels":[{"key":"cat"},{"key":"dog"}]}}]}',
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

    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir(parents=True)
    yaml.safe_dump(
        {"names": {0: "cat", 1: "dog"}, "nc": 2},
        (dataset_root / "data.yaml").open("w", encoding="utf-8"),
        allow_unicode=True,
    )

    with db.begin():
        job = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()

    names = _dataset_class_names_for_summary(job, dataset_root, prepared_classes=None)
    assert names == ["cat", "dog"]

    names2 = _dataset_class_names_for_summary(
        job, dataset_root, prepared_classes=["from_prepare"]
    )
    assert names2 == ["from_prepare"]


def test_train_loop_complete_when_epoch_reaches_target(tmp_path):
    work = tmp_path / "job-53"
    train_dir = work / "train" / "weights"
    train_dir.mkdir(parents=True)
    (train_dir / "best.pt").write_bytes(b"pt")
    results = work / "train" / "results.csv"
    results.write_text(
        "epoch,train/box_loss\n100,1.0\n",
        encoding="utf-8",
    )
    assert _is_ultralytics_train_loop_complete(work, 100) is True
    assert _is_ultralytics_train_loop_complete(work, 101) is False
    assert _is_ultralytics_train_loop_complete(work / "missing", 100) is False


def test_checkpoint_epoch_blocks_resume_when_epoch_minus_one(tmp_path):
    from labelu.internal.application.service.yolo_train import (
        _checkpoint_epoch_blocks_resume,
        _repair_checkpoint_epoch_before_resume,
    )

    work = tmp_path / "job-100"
    train_dir = work / "train"
    weights_dir = train_dir / "weights"
    weights_dir.mkdir(parents=True)
    last_pt = weights_dir / "last.pt"
    (train_dir / "results.csv").write_text(
        "epoch,train/box_loss\n11,1.0\n",
        encoding="utf-8",
    )
    torch = pytest.importorskip("torch")
    torch.save({"epoch": -1, "train_args": {"epochs": 200, "resume": False}}, last_pt)

    assert _checkpoint_epoch_blocks_resume(last_pt, work, target_epochs=200) is True
    assert (
        _repair_checkpoint_epoch_before_resume(
            last_pt, work, target_epochs=200, job_id=100
        )
        is True
    )
    ckpt = torch.load(last_pt, map_location="cpu", weights_only=False)
    assert ckpt["epoch"] == 11
    assert ckpt["train_args"]["epochs"] == 200
    assert _checkpoint_epoch_blocks_resume(last_pt, work, target_epochs=200) is False
