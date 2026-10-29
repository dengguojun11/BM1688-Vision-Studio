from __future__ import annotations

import json
from datetime import datetime

import pytest

from labelu.internal.adapter.persistence import crud_task, crud_yolo_preannotate_job
from labelu.internal.application.service import yolo_preannotate_worker_runtime as worker
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal, begin_transaction
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User
from pathlib import Path


def _job(db, user: User, options=None):
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="manual yolo worker",
                config=json.dumps({"tools": []}),
                media_type=MediaType.IMAGE.value,
                created_by=user.id,
                updated_by=user.id,
            ),
        )
        return crud_yolo_preannotate_job.create(
            db=db,
            task_id=task.id,
            user_id=user.id,
            sample_count=1,
            model_kind="trained",
            model_id=7,
            payload_json=json.dumps(
                {
                    "version": 1,
                    "task_id": task.id,
                    "user_id": user.id,
                    "sample_ids": [123],
                    "weights_path": "/tmp/model.pt",
                    "imgsz": 640,
                    "conf": 0.25,
                    "overwrite": True,
                    "model_kind": "trained",
                    "model_id": 7,
                    "job_type": "yolo_detection",
                    **(options or {}),
                }
            ),
        )


def test_claim_takeover_increments_token_and_old_worker_is_fenced(db):
    user = db.query(User).filter(User.username == "test@example.com").first()
    job = _job(db, user)
    with begin_transaction(db):
        first = crud_yolo_preannotate_job.claim_with_token(
            db, job_id=job.id, lease_owner="old", lease_seconds=60
        )
    assert first == 1
    with begin_transaction(db):
        row = crud_yolo_preannotate_job.get(db, job.id)
        row.lease_expires_at = datetime(2000, 1, 1)
    with begin_transaction(db):
        second = crud_yolo_preannotate_job.claim_with_token(
            db, job_id=job.id, lease_owner="new", lease_seconds=60
        )
    assert second == 2
    with begin_transaction(db):
        assert crud_yolo_preannotate_job.increment_progress_fenced(
            db,
            job_id=job.id,
            lease_owner="new",
            fencing_token=second,
            success=True,
        )
        assert not crud_yolo_preannotate_job.increment_progress_fenced(
            db,
            job_id=job.id,
            lease_owner="old",
            fencing_token=first,
            success=True,
        )


def test_worker_refuses_to_run_when_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)
    assert worker.run_worker_once() == 0


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"apply_to_canvas_overwrite": False, "agnostic_nms": True},
        {"apply_to_canvas_overwrite": True, "agnostic_nms": False},
    ],
)
def test_worker_claims_persisted_payload_and_reuses_sync_runtime(db, monkeypatch, options):
    user = db.query(User).filter(User.username == "test@example.com").first()
    job = _job(db, user, options)
    monkeypatch.setattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", True)
    seen = {}

    def fake_sync(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr(yolo_preannotate, "_run_yolo_preannotate_sync", fake_sync)
    assert worker.run_worker_once(limit=1) == 1
    assert seen["args"][0] == job.id
    assert seen["args"][1] == job.task_id
    assert seen["args"][2] == [123]
    assert seen["kwargs"]["lease_owner"].startswith("yolo-preannotate:")
    assert seen["kwargs"]["lease_fencing_token"] == 1
    assert seen["kwargs"]["apply_to_canvas_overwrite"] is options.get("apply_to_canvas_overwrite")
    assert seen["kwargs"]["agnostic_nms"] is options.get("agnostic_nms", False)
    # The worker closes the claiming session before running inference.  Verify
    # the lease survived that boundary instead of only observing the in-memory
    # update from the same SQLAlchemy session.
    with SessionLocal() as check_db:
        persisted = crud_yolo_preannotate_job.get(db=check_db, job_id=job.id)
        assert persisted.status == "PROCESSING"
        assert persisted.lease_owner == seen["kwargs"]["lease_owner"]
        assert persisted.lease_fencing_token == 1


def test_worker_converges_fatal_job_to_failed(db, monkeypatch):
    user = db.query(User).filter(User.username == "test@example.com").first()
    job = _job(db, user)
    monkeypatch.setattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", True)

    def fail_sync(*args, **kwargs):
        raise RuntimeError("contract changed")

    monkeypatch.setattr(yolo_preannotate, "_run_yolo_preannotate_sync", fail_sync)
    assert worker.run_worker_once(limit=1) == 1

    with SessionLocal() as check_db:
        persisted = crud_yolo_preannotate_job.get(db=check_db, job_id=job.id)
        assert persisted.status == "FAILED"
        assert persisted.error_message == "contract changed"
        assert persisted.lease_owner is None


def test_worker_loop_fails_closed_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)
    with pytest.raises(RuntimeError, match="拒绝启动"):
        worker.run_worker_loop(once=True)


def test_worker_payload_is_json_safe_and_contains_resolved_inputs():
    payload = yolo_preannotate._build_worker_payload(
        task_id=9,
        user_id=3,
        sample_ids=[8, 7],
        weights_path=Path("D:/models/best.pt"),
        imgsz=640,
        conf=0.35,
        overwrite=False,
        model_kind="trained",
        model_id=12,
        model_runtime={
            "job_type": "plate_recognition",
            "plate_artifacts": {"pose": Path("D:/models/pose.pt")},
            "plate_metadata": {"pose": {"class_names": ["车牌"]}},
        },
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)
    assert decoded["sample_ids"] == [8, 7]
    assert decoded["weights_path"] == str(Path("D:/models/best.pt"))
    assert decoded["plate_artifacts"]["pose"] == str(Path("D:/models/pose.pt"))
    assert decoded["job_type"] == "plate_recognition"
