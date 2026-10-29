from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from labelu.internal.adapter.persistence import crud_auto_label_job, crud_task
from labelu.internal.application.service import auto_preannotate_worker_runtime as worker
from labelu.internal.application.service import auto_sample_preannotate as service
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_label_job import AutoLabelJob
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User


def _job(db, user: User) -> AutoLabelJob:
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="worker fencing",
                config=json.dumps({"tools": []}),
                media_type=MediaType.IMAGE.value,
                created_by=user.id,
                updated_by=user.id,
            ),
        )
        return crud_auto_label_job.create(
            db=db,
            task_id=task.id,
            user_id=user.id,
            sample_count=1,
            filter_by_labels=True,
            job_kind=crud_auto_label_job.AUTO_SAMPLE_PREANNOTATE_JOB_KIND,
            payload_json=json.dumps({"sample_ids": [999]}),
        )


def test_reclaim_increments_fencing_token_and_old_owner_cannot_record(db, monkeypatch):
    user = db.query(User).filter(User.username == "test@example.com").first()
    job = _job(db, user)
    with begin_transaction(db):
        first = crud_auto_label_job.claim_auto_sample_job_with_token(
            db, job_id=job.id, lease_owner="old", lease_seconds=60
        )
    assert first == 1
    with begin_transaction(db):
        row = crud_auto_label_job.get(db, job.id)
        row.lease_expires_at = datetime(2000, 1, 1)
    with begin_transaction(db):
        second = crud_auto_label_job.claim_auto_sample_job_with_token(
            db, job_id=job.id, lease_owner="new", lease_seconds=60
        )
    assert second == 2
    with pytest.raises(RuntimeError, match="lease lost"):
        service._record_durable_job_sample(
            job.id,
            "old",
            sample_id=999,
            success=True,
            lease_fencing_token=first,
        )


def test_worker_refuses_to_run_when_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)
    called = {"value": False}
    monkeypatch.setattr(
        service,
        "_run_durable_auto_preannotate_job",
        lambda *args, **kwargs: called.__setitem__("value", True),
    )
    assert worker.run_worker_once() == 0
    assert called["value"] is False


def test_api_enqueue_does_not_start_thread_when_dedicated_worker_enabled(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED", True)
    assert service._submit_auto_preannotate_job(123456) is False

