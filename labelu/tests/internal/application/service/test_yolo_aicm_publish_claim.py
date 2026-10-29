"""AICM publication claim lease and fencing contract."""

from datetime import datetime, timedelta

from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export


def _export(db, **values):
    row = YoloBm1688Export(
        user_id=1,
        task_id=1,
        yolo_train_job_id=1,
        status="COMPLETED",
        imgsz=640,
        **values,
    )
    db.add(row)
    db.flush()
    return row


def test_publish_claim_is_single_owner_and_fenced(db):
    row = _export(db)
    claimed, reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="job-1",
        lease_owner="worker-1",
        lease_seconds=600,
    )
    assert claimed is not None
    assert reason == "CLAIMED"
    token = int(claimed.publish_fencing_token)

    rejected, reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="job-2",
        lease_owner="worker-2",
        lease_seconds=600,
    )
    assert rejected is None
    assert reason == "BUSY"

    assert (
        crud_yolo_bm1688_export.finish_publish_claim(
            db,
            export_id=row.id,
            job_id="job-1",
            lease_owner="worker-1",
            fencing_token=token - 1,
            status="COMPLETED",
            published_model_id=99,
        )
        is False
    )
    assert (
        crud_yolo_bm1688_export.finish_publish_claim(
            db,
            export_id=row.id,
            job_id="job-1",
            lease_owner="worker-1",
            fencing_token=token,
            status="COMPLETED",
            published_model_id=99,
        )
        is True
    )
    db.refresh(row)
    assert row.publish_status == "COMPLETED"
    assert row.published_model_id == 99
    assert row.publish_lease_owner is None


def test_expired_publish_claim_can_be_taken_over(db):
    row = _export(
        db,
        publish_status="PROCESSING",
        publish_job_id="old-job",
        publish_lease_owner="old-worker",
        publish_lease_expires_at=datetime.now() - timedelta(seconds=1),
        publish_fencing_token=3,
    )
    claimed, reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="new-job",
        lease_owner="new-worker",
        lease_seconds=600,
    )
    assert reason == "CLAIMED"
    assert claimed.publish_job_id == "new-job"
    assert claimed.publish_lease_owner == "new-worker"
    assert int(claimed.publish_fencing_token) == 4


def test_second_worker_cannot_claim_live_publish_lease(db):
    row = _export(db)
    first, first_reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="first-job",
        lease_owner="first-worker",
        lease_seconds=600,
    )
    assert first_reason == "CLAIMED"
    assert first is not None

    second, second_reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="second-job",
        lease_owner="second-worker",
        lease_seconds=600,
    )
    assert second is None
    assert second_reason == "BUSY"


def test_stale_worker_cannot_finish_after_lease_takeover(db):
    row = _export(
        db,
        publish_status="PROCESSING",
        publish_job_id="old-job",
        publish_lease_owner="old-worker",
        publish_lease_expires_at=datetime.now() - timedelta(seconds=1),
        publish_fencing_token=7,
    )
    replacement, replacement_reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="new-job",
        lease_owner="new-worker",
        lease_seconds=600,
    )
    assert replacement_reason == "CLAIMED"
    assert replacement is not None
    assert int(replacement.publish_fencing_token) == 8

    assert (
        crud_yolo_bm1688_export.finish_publish_claim(
            db,
            export_id=row.id,
            job_id="old-job",
            lease_owner="old-worker",
            fencing_token=7,
            status="COMPLETED",
            published_model_id=808,
        )
        is False
    )
    db.refresh(row)
    assert row.publish_status == "PROCESSING"
    assert row.published_model_id is None
    assert row.publish_lease_owner == "new-worker"


def test_publish_claim_renewal_requires_current_owner_and_token(db):
    row = _export(db)
    claimed, reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="job-renew",
        lease_owner="worker-renew",
        lease_seconds=600,
    )
    assert reason == "CLAIMED"
    token = int(claimed.publish_fencing_token)
    assert crud_yolo_bm1688_export.renew_publish_claim(
        db,
        export_id=row.id,
        job_id="job-renew",
        lease_owner="worker-renew",
        fencing_token=token,
        lease_seconds=600,
    )
    assert not crud_yolo_bm1688_export.renew_publish_claim(
        db,
        export_id=row.id,
        job_id="job-renew",
        lease_owner="stale-worker",
        fencing_token=token,
        lease_seconds=600,
    )


def test_completed_claim_cannot_be_claimed_again(db):
    row = _export(
        db,
        publish_status="COMPLETED",
        published_model_id=123,
        publish_job_id="finished-job",
    )
    claimed, reason = crud_yolo_bm1688_export.claim_publish(
        db,
        export_id=row.id,
        job_id="retry-job",
        lease_owner="retry-worker",
        lease_seconds=600,
    )
    assert claimed is None
    assert reason == "ALREADY_PUBLISHED"
