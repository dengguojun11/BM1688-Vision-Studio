from sqlalchemy.orm import sessionmaker

from labelu.internal.application.service import aicm_publish_worker_runtime as runtime
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export


def test_aicm_worker_disabled_does_not_touch_database(monkeypatch):
    monkeypatch.setattr(runtime, "dedicated_worker_enabled", lambda: False)
    result = runtime.run_worker_once(db_factory=lambda: (_ for _ in ()).throw(AssertionError()))
    assert result["enabled"] is False
    assert result["claimed"] == 0


def test_aicm_worker_fails_closed_when_schema_is_not_ready(monkeypatch):
    monkeypatch.setattr(runtime, "dedicated_worker_ready", lambda db_factory: (False, "缺少租约字段"))
    result = runtime.run_worker_once(
        db_factory=lambda: (_ for _ in ()).throw(AssertionError()), enabled=True
    )
    assert result["enabled"] is True
    assert result["ready"] is False
    assert result["claimed"] == 0
    assert result["reason"] == "缺少租约字段"


def test_aicm_worker_claims_and_replays_persisted_payload(db, tmp_path, monkeypatch):
    export = YoloBm1688Export(
        user_id=1,
        task_id=1,
        yolo_train_job_id=1,
        status="COMPLETED",
        imgsz=640,
        publish_status="PENDING",
        publish_job_id="publish-job-1",
    )
    db.add(export)
    db.flush()
    db.commit()
    monkeypatch.setattr(runtime, "dedicated_worker_ready", lambda db_factory: (True, "ok"))
    monkeypatch.setattr(
        runtime,
        "_candidate_ids",
        lambda db, now, limit: [(int(export.id), "publish-job-1")],
    )
    monkeypatch.setattr(
        runtime,
        "load_job",
        lambda job_id: {
            "job_id": job_id,
            "user_id": 7,
            "environment_id": "prod",
            "keep_template_cache": True,
            "bmodel_precision": "int8",
            "bmodel_rel_path": "exports/int8.bmodel",
            "publish_mode": "manual",
        },
    )
    calls = []

    def run_job(*args):
        calls.append(args)

    SessionFactory = sessionmaker(bind=db.get_bind(), autoflush=False)
    result = runtime.run_worker_once(
        db_factory=SessionFactory,
        owner="worker-a",
        enabled=True,
        run_job=run_job,
    )
    assert result["claimed"] == 1
    assert result["failed"] == 0
    assert len(calls) == 1
    assert calls[0][0:9] == (
        "publish-job-1",
        int(export.id),
        7,
        "prod",
        True,
        "int8",
        "exports/int8.bmodel",
        "manual",
        "worker-a",
    )
    assert int(calls[0][9]) == 1


def test_aicm_worker_reports_missing_payload_as_fenced_failure(db, monkeypatch):
    export = YoloBm1688Export(
        user_id=1,
        task_id=1,
        yolo_train_job_id=1,
        status="COMPLETED",
        imgsz=640,
        publish_status="PENDING",
        publish_job_id="publish-job-missing",
    )
    db.add(export)
    db.flush()
    db.commit()
    monkeypatch.setattr(runtime, "dedicated_worker_ready", lambda db_factory: (True, "ok"))
    monkeypatch.setattr(
        runtime,
        "_candidate_ids",
        lambda db, now, limit: [(int(export.id), "publish-job-missing")],
    )
    monkeypatch.setattr(runtime, "load_job", lambda job_id: {})
    SessionFactory = sessionmaker(bind=db.get_bind(), autoflush=False)
    result = runtime.run_worker_once(
        db_factory=SessionFactory, owner="worker-b", enabled=True
    )
    assert result["claimed"] == 1
    assert result["failed"] == 1
    db.refresh(export)
    assert export.publish_status == "FAILED"
    assert export.publish_lease_owner is None
