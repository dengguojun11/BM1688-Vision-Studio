from datetime import datetime, timedelta, timezone

import json

from labelu.internal.adapter.persistence import crud_task, crud_user
from labelu.internal.application.service import yolo_train_gt_pred_compare as svc
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus


def test_stale_running_no_progress():
    started = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    assert svc._is_stale_running_status(
        {"status": "running", "started_at": started, "processed": 0}
    )


def test_not_stale_when_recent():
    started = datetime.now(timezone.utc).isoformat()
    assert not svc._is_stale_running_status(
        {"status": "running", "started_at": started, "processed": 0}
    )


def test_effective_status_completed_when_manifest_exists(tmp_path, monkeypatch):
    job_id = 99

    def fake_root(jid):
        return tmp_path / f"job-{jid}"

    monkeypatch.setattr(svc, "yolo_train_work_root", fake_root)
    root = fake_root(job_id) / "reports" / "gt_pred_compare"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text('{"items":[]}', encoding="utf-8")
    (root / "status.json").write_text(
        '{"status":"running","processed":0,"started_at":"2020-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    out = svc.read_compare_status(job_id)
    assert out["status"] == "completed"


def test_restart_recovery_can_schedule_missing_idle_compare(tmp_path, monkeypatch):
    job_id = 100

    def fake_root(jid):
        return tmp_path / f"job-{jid}"

    monkeypatch.setattr(svc, "yolo_train_work_root", fake_root)
    scheduled: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        svc,
        "schedule_gt_pred_compare_after_train",
        lambda jid, recover_interrupted=False: scheduled.append(
            (jid, recover_interrupted)
        )
        or True,
    )

    assert not svc.recover_gt_pred_compare_after_restart(job_id)
    assert svc.recover_gt_pred_compare_after_restart(job_id, include_idle=True)
    assert scheduled == [(job_id, True)]


def test_dataset_image_counts(tmp_path, monkeypatch):
    job_id = 7

    def fake_root(jid):
        return tmp_path / f"job-{jid}"

    monkeypatch.setattr(svc, "yolo_train_work_root", fake_root)
    img_root = fake_root(job_id) / "dataset" / "images"
    (img_root / "train").mkdir(parents=True)
    (img_root / "val").mkdir(parents=True)
    (img_root / "test").mkdir(parents=True)
    (img_root / "train" / "a.jpg").write_bytes(b"x")
    (img_root / "val" / "b.png").write_bytes(b"x")
    (img_root / "val" / "c.png").write_bytes(b"x")
    (img_root / "test" / "d.jpg").write_bytes(b"x")

    counts = svc._dataset_image_counts(job_id)
    assert counts == {"train": 1, "val": 2, "test": 1, "total": 4}


def test_discover_dataset_splits(tmp_path, monkeypatch):
    job_id = 8

    def fake_root(jid):
        return tmp_path / f"job-{jid}"

    monkeypatch.setattr(svc, "yolo_train_work_root", fake_root)
    img_root = fake_root(job_id) / "dataset" / "images"
    (img_root / "train").mkdir(parents=True)
    (img_root / "val").mkdir(parents=True)
    (img_root / "train" / "a.jpg").write_bytes(b"x")
    (img_root / "val" / "b.png").write_bytes(b"x")

    assert svc._discover_dataset_splits(job_id) == ["train", "val"]


def test_resolve_compare_splits_uses_all_dataset_by_default(tmp_path, monkeypatch):
    job_id = 9

    def fake_root(jid):
        return tmp_path / f"job-{jid}"

    monkeypatch.setattr(svc, "yolo_train_work_root", fake_root)
    img_root = fake_root(job_id) / "dataset" / "images"
    for split in ("train", "val", "test"):
        (img_root / split).mkdir(parents=True)
        (img_root / split / f"{split}.jpg").write_bytes(b"x")

    monkeypatch.setattr(svc.settings, "YOLO_GT_PRED_COMPARE_USE_ALL_DATASET_SPLITS", True)
    assert svc._resolve_compare_splits(job_id) == ["train", "val", "test"]


def test_plate_recognition_compare_uses_pose_dataset(tmp_path, monkeypatch):
    job_id = 253

    def fake_root(jid):
        return tmp_path / f"job-{jid}"

    monkeypatch.setattr(svc, "yolo_train_work_root", fake_root)
    job = type("Job", (), {"job_type": "plate_recognition"})()
    dataset_root = fake_root(job_id) / "plate_dataset" / "pose"
    for split in ("train", "val", "test"):
        image_dir = dataset_root / "images" / split
        image_dir.mkdir(parents=True)
        (image_dir / f"{split}.jpg").write_bytes(b"x")

    assert svc._dataset_root_for_job(job_id, job) == dataset_root
    assert svc._discover_splits_from_dataset_root(dataset_root) == [
        "train",
        "val",
        "test",
    ]
    assert svc._dataset_image_counts_from_root(dataset_root) == {
        "train": 1,
        "val": 1,
        "test": 1,
        "total": 3,
    }


def test_resolve_compare_sample_reference_prefers_pool_source_meta(db):
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        source_task = crud_task.create(
            db,
            Task(
                name="source-task",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        pool_task = crud_task.create(
            db,
            Task(
                name="pool-task",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "consistent_pool": {
                            "kind": "train_filtered_consistent",
                            "category": "垃圾暴露",
                        }
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        source_sample = TaskSample(
            task_id=source_task.id,
            created_by=current_user.id,
            updated_by=current_user.id,
            state=SampleState.DONE.value,
            data="{}",
        )
        pool_sample = TaskSample(
            task_id=pool_task.id,
            created_by=current_user.id,
            updated_by=current_user.id,
            state=SampleState.DONE.value,
            data=json.dumps(
                {
                    "_consistent_pool": {
                        "source_task_id": source_task.id,
                        "source_sample_id": 4321,
                    }
                },
                ensure_ascii=False,
            ),
        )
        db.add(source_sample)
        db.add(pool_sample)
        db.flush()

    ref = svc._resolve_compare_sample_reference(db, pool_sample.id)

    assert ref == {
        "sample_id": 4321,
        "task_id": source_task.id,
        "compare_sample_id": pool_sample.id,
        "compare_task_id": pool_task.id,
    }

