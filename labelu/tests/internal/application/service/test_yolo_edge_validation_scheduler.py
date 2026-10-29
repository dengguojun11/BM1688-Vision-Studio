from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from labelu.internal.adapter.persistence import crud_yolo_edge_validation_job
from labelu.internal.application.service import (
    yolo_edge_validation_scheduler as scheduler,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


def _job(*, offset_seconds: int, status: str = "PENDING") -> YoloEdgeValidationJob:
    now = datetime.now()
    return YoloEdgeValidationJob(
        user_id=1,
        task_id=1,
        export_id=100 + offset_seconds,
        status=status,
        mode="run_only",
        threshold_source="system_default",
        sample_ids_json="[]",
        thresholds_json="{}",
        image_count=1,
        labeled_image_count=0,
        unlabeled_image_count=1,
        queued_at=now + timedelta(seconds=offset_seconds),
        queue_reason="waiting_for_dispatch",
    )


def _install_devices(monkeypatch):
    checked_at = datetime.now(timezone.utc).isoformat()
    devices = [
        {
            "id": "edge-a",
            "name": "A",
            "enabled": True,
            "host": "10.0.0.1",
            "user": "root",
            "last_probe_status": "ok",
            "last_probe_checked_at": checked_at,
            "environment_status": "ready",
            "environment_report": {"environment_ready": True},
            "last_used_at": None,
        },
        {
            "id": "edge-b",
            "name": "B",
            "enabled": True,
            "host": "10.0.0.2",
            "user": "root",
            "last_probe_status": "ok",
            "last_probe_checked_at": checked_at,
            "environment_status": "ready",
            "environment_report": {"environment_ready": True},
            "last_used_at": (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat(),
        },
    ]

    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "list_device_records",
        lambda: devices,
    )

    def _config(device_id: str) -> dict:
        record = next(item for item in devices if item["id"] == device_id)
        return {
            **record,
            "port": 22,
            "password": "secret",
            "remote_base": "/data/labelu_edge/jobs",
            "validation_timeout_sec": 7200,
        }

    monkeypatch.setattr(scheduler.edge_device_registry, "load_device_config", _config)
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "get_active_device_id",
        lambda: "edge-a",
    )

    def _mark_used(device_id: str):
        record = next(item for item in devices if item["id"] == device_id)
        record["last_used_at"] = datetime.now(timezone.utc).isoformat()
        return record

    monkeypatch.setattr(scheduler.edge_device_registry, "mark_device_used", _mark_used)
    return devices


@pytest.mark.parametrize(
    "record",
    [
        {
            "agent_bootstrap": {"status": "QUEUED"},
            "environment_status": "ready",
            "environment_report": {"environment_ready": True},
        },
        {
            "environment_report": {
                "environment_ready": True,
                "agent_bootstrap": {"status": "INSTALLING"},
            },
            "environment_status": "ready",
        },
        {
            "agent_bootstrap": {},
            "environment_report": {
                "environment_ready": True,
                "agent_bootstrap": {"status": "WAITING_HEARTBEAT"},
            },
            "environment_status": "ready",
        },
        {
            "environment_status": "checking",
            "environment_code": "agent_bootstrap_in_progress",
            "environment_report": {"environment_ready": True},
        },
    ],
)
def test_agent_bootstrap_in_progress_excludes_ready_device(record):
    """A persisted bootstrap marker must win over a stale ready report."""

    assert scheduler._agent_bootstrap_in_progress(record)
    assert not scheduler._environment_is_ready(record)


def test_completed_agent_bootstrap_does_not_keep_device_blocked():
    record = {
        "agent_bootstrap": {"status": "READY"},
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }

    assert not scheduler._agent_bootstrap_in_progress(record)
    assert scheduler._environment_is_ready(record)


def test_auto_agent_control_plane_rejects_failed_ssh_bootstrap(monkeypatch):
    monkeypatch.setattr(scheduler.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(scheduler.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(scheduler.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(scheduler.settings, "BOARD_AGENT_TOKEN", "agent-token")
    record = {
        "validation_channel": "ssh",
        "agent_bootstrap": {"status": "FAILED"},
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }

    assert not scheduler._environment_is_ready(record)


def test_failed_board_agent_bootstrap_rejects_ready_heartbeat():
    record = {
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
        "agent_bootstrap": {"status": "FAILED", "code": "install_failed"},
    }

    assert not scheduler._environment_is_ready(record)


def test_failed_board_agent_bootstrap_stays_blocked_when_control_plane_is_disabled(
    monkeypatch,
):
    """Disabling automatic enrollment must not clear a persisted failure."""

    monkeypatch.setattr(scheduler.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", False)
    monkeypatch.setattr(scheduler.settings, "BOARD_AGENT_ENABLED", False)
    monkeypatch.setattr(scheduler.settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(scheduler.settings, "BOARD_AGENT_TOKEN", "")
    record = {
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
        "agent_bootstrap": {"status": "FAILED", "code": "install_failed"},
    }

    assert not scheduler._environment_is_ready(record)


def test_manual_board_agent_without_bootstrap_status_uses_heartbeat_environment():
    record = {
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
    }

    assert scheduler._environment_is_ready(record)


def test_future_board_agent_heartbeat_is_not_fresh():
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    record = {
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": (now + timedelta(hours=1)).isoformat(),
    }

    assert scheduler._board_agent_heartbeat_is_fresh(record, now=now) is False


def test_future_ssh_probe_is_not_fresh():
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    record = {
        "last_probe_status": "ok",
        "last_probe_checked_at": (now + timedelta(hours=1)).isoformat(),
    }

    assert scheduler._probe_is_fresh(record, now=now) is False


def test_board_agent_heartbeat_expiring_before_claim_keeps_job_retryable(
    db, monkeypatch
):
    """The final claim must fail closed when the cached heartbeat expires."""

    checked_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": "board-edge",
        "name": "Board edge",
        "enabled": True,
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": checked_at,
        "board_agent_environment_ready": True,
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "last_used_at": None,
    }
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(
        scheduler.edge_device_registry, "list_device_records", lambda: [record]
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "load_device_config",
        lambda _device_id: {
            **record,
            "port": 22,
            "user": "board-agent",
            "validation_timeout_sec": 7200,
        },
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "mark_device_used",
        lambda _device_id: record,
    )
    heartbeat_checks = iter((True, False))
    monkeypatch.setattr(
        scheduler,
        "_board_agent_heartbeat_is_fresh",
        lambda _record: next(heartbeat_checks),
    )
    row = _job(offset_seconds=60)
    db.add(row)
    db.commit()
    job_id = int(row.id)

    assert scheduler.dispatch_pending_edge_validation_jobs(start_workers=False) == []

    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, job_id)
    assert refreshed.status == "PENDING"
    assert refreshed.edge_device_id is None
    assert refreshed.queue_reason == "waiting_for_online_device"


def test_ssh_probe_expiring_before_final_claim_keeps_job_pending(db, monkeypatch):
    """A cached SSH probe must still be fresh when the lease is written."""

    checked_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": "ssh-edge",
        "name": "SSH edge",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "10.0.0.8",
        "user": "root",
        "last_probe_status": "ok",
        "last_probe_checked_at": checked_at,
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "last_used_at": None,
    }
    monkeypatch.setattr(
        scheduler.edge_device_registry, "list_device_records", lambda: [record]
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "load_device_config",
        lambda _device_id: {
            **record,
            "port": 22,
            "password": "secret",
            "remote_base": "/data/labelu_edge/jobs",
            "validation_timeout_sec": 7200,
        },
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "mark_device_used",
        lambda _device_id: record,
    )
    # Candidate selection sees a valid probe; the final claim observes that
    # the TTL has elapsed and must leave the database row untouched.
    probe_checks = iter((True, False))
    monkeypatch.setattr(
        scheduler, "_probe_is_fresh", lambda _record: next(probe_checks)
    )
    row = _job(offset_seconds=60)
    db.add(row)
    db.commit()
    job_id = int(row.id)

    assert scheduler.dispatch_pending_edge_validation_jobs(start_workers=False) == []

    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, job_id)
    assert refreshed.status == "PENDING"
    assert refreshed.edge_device_id is None
    assert refreshed.queue_reason == "waiting_for_online_device"


def test_dispatch_assigns_fifo_jobs_to_distinct_idle_devices(db, monkeypatch):
    _install_devices(monkeypatch)
    rows = [_job(offset_seconds=index) for index in range(3)]
    db.add_all(rows)
    db.commit()
    job_ids = [int(row.id) for row in rows]

    assert scheduler.queue_position(db, rows[0]) == 1
    assert scheduler.queue_position(db, rows[1]) == 2
    assert scheduler.queue_position(db, rows[2]) == 3

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == job_ids[:2]
    db.expire_all()
    refreshed = [db.get(YoloEdgeValidationJob, job_id) for job_id in job_ids]
    assert [row.edge_device_id for row in refreshed[:2]] == ["edge-a", "edge-b"]
    assert [row.status for row in refreshed] == ["PROCESSING", "PROCESSING", "PENDING"]
    assert refreshed[2].queue_reason == "waiting_for_idle_device"
    assert scheduler.queue_position(db, refreshed[2]) is None


def test_dispatch_claims_mixed_pending_and_queued_rows_in_one_fifo_lane(
    db, monkeypatch
):
    """Persisted PENDING and QUEUED rows share dispatch and lease semantics."""

    _install_devices(monkeypatch)
    rows = [
        _job(offset_seconds=60, status="QUEUED"),
        _job(offset_seconds=61, status="PENDING"),
        _job(offset_seconds=62, status="QUEUED"),
    ]
    db.add_all(rows)
    db.commit()
    job_ids = [int(row.id) for row in rows]

    assert scheduler.queue_position(db, rows[0]) == 1
    assert scheduler.queue_position(db, rows[1]) == 2
    assert scheduler.queue_position(db, rows[2]) == 3

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == job_ids[:2]
    db.expire_all()
    refreshed = [db.get(YoloEdgeValidationJob, job_id) for job_id in job_ids]
    assert [row.status for row in refreshed] == [
        "PROCESSING",
        "PROCESSING",
        "QUEUED",
    ]
    assert [row.edge_device_id for row in refreshed[:2]] == ["edge-a", "edge-b"]
    for row in refreshed[:2]:
        assert row.assigned_at is not None
        assert row.lease_expires_at is not None
        assert row.lease_expires_at > row.assigned_at
    assert scheduler.queue_position(db, refreshed[2]) is None


def test_queue_position_excludes_derived_queue_states(db):
    """Only the plain dispatch lane consumes a visible FIFO position."""

    rows = [
        _job(offset_seconds=70),
        _job(offset_seconds=71),
        _job(offset_seconds=72),
        _job(offset_seconds=73),
    ]
    rows[0].queue_reason = "retry_scheduled"
    rows[1].queue_reason = "preparing_pack"
    rows[2].queue_reason = "waiting_for_agent"
    rows[3].status = "QUEUED"
    db.add_all(rows)
    db.commit()

    assert scheduler.queue_position(db, rows[0]) is None
    assert scheduler.queue_position(db, rows[1]) is None
    assert scheduler.queue_position(db, rows[2]) is None
    assert scheduler.queue_position(db, rows[3]) == 1


def test_queued_row_is_an_active_export_for_duplicate_protection(db):
    """The export-level active-job gate must cover the newer QUEUED state."""

    row = _job(offset_seconds=80, status="QUEUED")
    db.add(row)
    db.commit()

    assert (
        crud_yolo_edge_validation_job.has_active_for_export(db, export_id=row.export_id)
        is True
    )
    latest = crud_yolo_edge_validation_job.get_latest_active_for_export(
        db, export_id=row.export_id
    )
    assert latest is not None
    assert latest.id == row.id


def test_dedicated_edge_worker_not_ready_does_not_fall_back_to_web_thread(
    db, monkeypatch
):
    _install_devices(monkeypatch)
    row = _job(offset_seconds=3)
    db.add(row)
    db.commit()

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_validation_worker_runtime.dedicated_worker_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_validation_worker_runtime.dedicated_worker_ready",
        lambda: (False, "边端验证表缺少租约字段"),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._run_manual_job_sync",
        lambda *_args, **_kwargs: pytest.fail("Worker 未就绪时不应回落 Web 边端线程"),
    )

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=True)
    assert assigned == [int(row.id)]


def test_released_device_receives_next_fifo_job(db, monkeypatch):
    _install_devices(monkeypatch)
    rows = [_job(offset_seconds=index + 10) for index in range(3)]
    db.add_all(rows)
    db.commit()
    job_ids = [int(row.id) for row in rows]
    scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    db.expire_all()
    first = db.get(YoloEdgeValidationJob, job_ids[0])
    with begin_transaction(db):
        first.status = "COMPLETED"
        first.completed_at = datetime.now()
        db.add(first)

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == [job_ids[2]]
    db.expire_all()
    third = db.get(YoloEdgeValidationJob, job_ids[2])
    assert third.status == "PROCESSING"
    assert third.edge_device_id == "edge-a"


def test_recovery_job_keeps_its_previously_assigned_device(db, monkeypatch):
    _install_devices(monkeypatch)
    row = _job(offset_seconds=20)
    row.edge_device_id = "edge-b"
    db.add(row)
    db.commit()
    job_id = int(row.id)

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == [job_id]
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, job_id)
    assert refreshed.edge_device_id == "edge-b"


def test_repeated_export_prefers_latest_completed_device(db, monkeypatch):
    _install_devices(monkeypatch)
    previous = _job(offset_seconds=21)
    previous.status = "COMPLETED"
    previous.edge_device_id = "edge-b"
    previous.completed_at = datetime.now()
    repeated = _job(offset_seconds=22)
    repeated.export_id = previous.export_id
    db.add_all([previous, repeated])
    db.commit()
    repeated_id = int(repeated.id)

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == [repeated_id]
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, repeated_id)
    assert refreshed.edge_device_id == "edge-b"


def test_repeated_export_falls_back_when_affinity_device_is_busy(db, monkeypatch):
    _install_devices(monkeypatch)
    previous = _job(offset_seconds=23)
    previous.status = "COMPLETED"
    previous.edge_device_id = "edge-b"
    previous.completed_at = datetime.now()
    busy = _job(offset_seconds=24)
    busy.status = "PROCESSING"
    busy.edge_device_id = "edge-b"
    repeated = _job(offset_seconds=25)
    repeated.export_id = previous.export_id
    db.add_all([previous, busy, repeated])
    db.commit()
    repeated_id = int(repeated.id)

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == [repeated_id]
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, repeated_id)
    assert refreshed.edge_device_id == "edge-a"


def test_environment_not_ready_device_cannot_receive_job(db, monkeypatch):
    devices = _install_devices(monkeypatch)
    for device in devices:
        device["environment_status"] = "blocked"
        device["environment_report"] = {
            "environment_ready": False,
            "fatal_reasons": ["tmp_disk_space_insufficient"],
        }
    row = _job(offset_seconds=30)
    db.add(row)
    db.commit()
    job_id = int(row.id)

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == []
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, job_id)
    assert refreshed.status == "PENDING"
    assert refreshed.edge_device_id is None
    assert refreshed.queue_reason == "waiting_for_environment_ready"
    assert "尚未通过 BM1688 环境检查" in refreshed.report_summary


def test_phase_one_gate_dispatches_manual_job_and_holds_automatic_job(db, monkeypatch):
    _install_devices(monkeypatch)
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    automatic = _job(offset_seconds=40)
    automatic.auto_training_plan_id = 10
    automatic.auto_training_round_id = 20
    manual = _job(offset_seconds=41)
    db.add_all([automatic, manual])
    db.commit()
    automatic_id = int(automatic.id)
    manual_id = int(manual.id)

    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)

    assert assigned == [manual_id]
    db.expire_all()
    automatic_row = db.get(YoloEdgeValidationJob, automatic_id)
    manual_row = db.get(YoloEdgeValidationJob, manual_id)
    assert automatic_row.status == "PENDING"
    assert automatic_row.edge_device_id is None
    assert scheduler.queue_position(db, automatic_row) is None
    assert manual_row.status == "PROCESSING"
    assert manual_row.edge_device_id == "edge-a"


def test_scheduler_does_not_claim_automatic_job_until_pack_is_ready(
    db, monkeypatch, tmp_path
):
    _install_devices(monkeypatch)
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    row = _job(offset_seconds=50)
    row.auto_training_plan_id = 11
    row.auto_training_round_id = 51
    db.add(row)
    db.commit()
    job_id = int(row.id)

    assert scheduler.dispatch_pending_edge_validation_jobs(start_workers=False) == []
    db.expire_all()
    assert db.get(YoloEdgeValidationJob, job_id).status == "PENDING"

    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    row = db.get(YoloEdgeValidationJob, job_id)
    row.result_json = json.dumps(
        {
            "pack": {
                "state": "READY",
                "manifest_path": str(manifest),
            }
        }
    )
    db.commit()

    assert scheduler.dispatch_pending_edge_validation_jobs(start_workers=False) == [
        job_id
    ]
    db.expire_all()
    assert db.get(YoloEdgeValidationJob, job_id).status == "PROCESSING"


def test_scheduler_persists_unready_pack_projection(db, tmp_path):
    row = _job(offset_seconds=60)
    row.result_json = json.dumps(
        {
            "pack": {
                "state": "READY",
                "manifest_path": str(tmp_path / "missing-manifest.json"),
            }
        }
    )
    db.add(row)
    db.commit()

    assert scheduler._pending_jobs(db) == []
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, row.id)
    assert refreshed.queue_reason == "waiting_for_pack"
    assert refreshed.edge_device_id is None
