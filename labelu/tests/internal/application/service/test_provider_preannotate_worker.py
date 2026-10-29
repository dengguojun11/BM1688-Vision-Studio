"""Dedicated provider preannotation queue contract tests."""

from datetime import datetime, timedelta

from labelu.internal.adapter.persistence import (
    crud_locateanything_preannotate_job,
    crud_tagtool_preannotate_job,
)
from labelu.internal.application.service import provider_preannotate_worker_runtime as runtime
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.locateanything_preannotate_job import LocateAnythingPreannotateJob
from labelu.internal.domain.models.tagtool_preannotate_job import TagtoolPreannotateJob


def test_tagtool_claim_and_old_worker_fencing(db):
    with begin_transaction(db):
        row = TagtoolPreannotateJob(
            task_id=1, created_by=1, sample_count=1, model_kind="imported", model_id=7,
            payload_json='{"sample_ids":[1]}',
        )
        db.add(row)
        db.flush()
        job_id = int(row.id)

    with begin_transaction(db):
        first = crud_tagtool_preannotate_job.claim_with_token(
            db, job_id=job_id, lease_owner="worker-a", lease_seconds=30,
        )
    assert first == 1

    with begin_transaction(db):
        db.query(TagtoolPreannotateJob).filter(TagtoolPreannotateJob.id == job_id).update(
            {TagtoolPreannotateJob.lease_expires_at: datetime.now() - timedelta(seconds=1)}
        )
        second = crud_tagtool_preannotate_job.claim_with_token(
            db, job_id=job_id, lease_owner="worker-b", lease_seconds=30,
        )
    assert second == 2

    with begin_transaction(db):
        assert not crud_tagtool_preannotate_job.increment_progress_fenced(
            db, job_id=job_id, lease_owner="worker-a", lease_fencing_token=1, success=True,
        )
        assert crud_tagtool_preannotate_job.increment_progress_fenced(
            db, job_id=job_id, lease_owner="worker-b", lease_fencing_token=2, success=True,
        )


def test_provider_renew_requires_current_fencing_generation(db):
    with begin_transaction(db):
        row = LocateAnythingPreannotateJob(
            task_id=4, created_by=1, provider="rex", sample_count=1,
            payload_json='{"sample_ids":[4]}',
        )
        db.add(row)
        db.flush()
        job_id = int(row.id)
    with begin_transaction(db):
        token = crud_locateanything_preannotate_job.claim_with_token(
            db, job_id=job_id, lease_owner="worker-r", lease_seconds=30, provider="rex",
        )
    assert token == 1
    with begin_transaction(db):
        assert crud_locateanything_preannotate_job.renew_fenced(
            db, job_id=job_id, lease_owner="worker-r", lease_fencing_token=1,
            lease_seconds=60,
        )
        assert not crud_locateanything_preannotate_job.renew_fenced(
            db, job_id=job_id, lease_owner="stale", lease_fencing_token=1,
            lease_seconds=60,
        )


def test_shared_provider_job_isolated_by_provider_and_payload(db):
    with begin_transaction(db):
        for provider in ("locateanything", "vlx", "rex"):
            db.add(
                LocateAnythingPreannotateJob(
                    task_id=2, created_by=1, provider=provider, sample_count=1,
                    payload_json='{"sample_ids":[2],"provider":"%s"}' % provider,
                )
            )
        db.flush()

    assert crud_locateanything_preannotate_job.get_active_by_task_id(
        db, 2, provider="vlx"
    ).provider == "vlx"
    assert crud_locateanything_preannotate_job.list_runnable(db, provider="rex", limit=10)[0].provider == "rex"


def test_worker_refuses_to_run_when_disabled(monkeypatch):
    monkeypatch.setattr(runtime.settings, "PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)
    assert runtime.run_worker_once() == 0


def test_worker_dispatches_persisted_vlx_payload_with_fencing(db, monkeypatch):
    with begin_transaction(db):
        row = LocateAnythingPreannotateJob(
            task_id=3, created_by=1, provider="vlx", sample_count=1,
            payload_json='{"sample_ids":[9],"task_config":{"tools":[]},"target_labels":["car"],"overwrite":true,"created_by":1,"provider":"vlx"}',
        )
        db.add(row)
        db.flush()
    monkeypatch.setattr(runtime.settings, "PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(runtime.settings, "PROVIDER_PREANNOTATE_WORKER_BATCH_LIMIT", 1)
    monkeypatch.setattr(runtime.settings, "PROVIDER_PREANNOTATE_MAX_CONCURRENT_JOBS", 1)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(runtime.vlx_seek_preannotate, "_run_batch_sync", fake_run)
    assert runtime.run_worker_once() == 1
    assert captured["args"][:5] == (1, 3, [9], {"tools": []}, ["car"])
    assert captured["args"][7] == "vlx"
    assert captured["kwargs"]["lease_fencing_token"] == 1
