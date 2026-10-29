from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from threading import Event

import pytest

from labelu.internal.application.service import yolo_edge_validation as mod
from labelu.internal.application.service.yolo_edge_validation_auto_resume import (
    maybe_auto_resume_edge_validation_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


def test_processing_job_with_fresh_heartbeat_is_not_resumable():
    row = SimpleNamespace(
        status="PROCESSING",
        started_at=datetime.now() - timedelta(minutes=10),
        updated_at=datetime.now(),
        created_at=datetime.now() - timedelta(minutes=20),
    )
    assert mod.is_stale_edge_validation_job(row) is False
    assert mod.can_resume_edge_validation_job(row) is False


def test_processing_job_with_stale_heartbeat_is_resumable():
    row = SimpleNamespace(
        status="PROCESSING",
        started_at=datetime.now() - timedelta(minutes=10),
        updated_at=datetime.now() - timedelta(minutes=6),
        created_at=datetime.now() - timedelta(minutes=20),
    )
    assert mod.is_stale_edge_validation_job(row) is True
    assert mod.can_resume_edge_validation_job(row) is True


def test_fresh_processing_job_is_not_resumable():
    row = SimpleNamespace(
        status="PROCESSING",
        started_at=datetime.now(),
        updated_at=datetime.now(),
        created_at=datetime.now(),
    )
    assert mod.is_stale_edge_validation_job(row) is False
    assert mod.can_resume_edge_validation_job(row) is False


def test_manual_run_does_not_ssh_before_committed_ready_pack(db, monkeypatch):
    with begin_transaction(db):
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=1,
            status="PROCESSING",
            mode="run_only",
            auto_training_plan_id=1,
            auto_training_round_id=99001,
            pipeline_origin="AUTO_QUALITY",
            edge_device_id="edge-a",
            edge_host="127.0.0.1",
            edge_port=22,
            edge_user="root",
            started_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(row)
        db.flush()
        job_id = int(row.id)

    monkeypatch.setattr(
        mod.edge_ssh_validation,
        "ssh_connect",
        lambda *_args, **_kwargs: pytest.fail("SSH must not start without READY pack"),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.schedule_edge_validation_dispatch",
        lambda: None,
    )
    mod._run_manual_job_sync(job_id)

    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, job_id)
    assert refreshed.status == "PENDING"
    assert refreshed.queue_reason == "pack_not_ready"
    assert refreshed.edge_device_id is None


def test_start_job_does_not_launch_ssh_runner_for_board_agent_assignment(monkeypatch):
    row = SimpleNamespace(
        id=92,
        status="PROCESSING",
        edge_device_id="board-a",
        started_at=datetime.now() - timedelta(minutes=10),
        updated_at=datetime.now() - timedelta(minutes=10),
    )
    task = SimpleNamespace(name="board task")
    db = SimpleNamespace(
        expire=lambda _row: None,
        refresh=lambda _row: None,
    )

    monkeypatch.setattr(
        mod,
        "_load_job_with_access",
        lambda _db, **_kwargs: (row, task),
    )
    monkeypatch.setattr(mod, "is_stale_edge_validation_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        mod,
        "_registered_board_agent_device",
        lambda _device_id: True,
    )
    monkeypatch.setattr(
        mod,
        "_to_detail_response",
        lambda *_args, **_kwargs: {"id": row.id},
    )
    monkeypatch.setattr(
        mod,
        "edge_ssh_validation",
        SimpleNamespace(_edge_ssh_configured=lambda: False),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.has_configured_edge_devices",
        lambda: True,
    )
    enqueued = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.enqueue_edge_validation_job",
        lambda job_id, **kwargs: enqueued.append((job_id, kwargs)),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.queue_position",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        mod.threading,
        "Thread",
        lambda **_kwargs: pytest.fail("Board Agent job must not launch SSH Runner"),
    )

    result = mod.start_job(db, job_id=92, current_user=SimpleNamespace(id=1))

    assert result == {"id": 92}
    assert enqueued == [(92, {"preserve_assigned_device": True})]


def test_manual_job_heartbeat_refreshes_long_running_stage(monkeypatch):
    touched = Event()
    monkeypatch.setattr(
        mod,
        "_touch_manual_job_heartbeat",
        lambda job_id: touched.set() if job_id == 42 else None,
    )

    with mod._manual_job_heartbeat(42, interval_seconds=0.01):
        assert touched.wait(timeout=1.0)


def test_manual_job_heartbeat_uses_execution_fencing(db):
    """A stale worker must not refresh the heartbeat after takeover."""
    with begin_transaction(db):
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=902,
            status="PROCESSING",
            mode="run_only",
            edge_device_id="edge-a",
            updated_at=datetime.now() - timedelta(minutes=5),
        )
        db.add(row)
        db.flush()
        job_id = int(row.id)

    calls = []

    class _Monitor:
        def conditional_update(self, values, *, db, manage_transaction):
            calls.append((values, db, manage_transaction))

    mod._touch_manual_job_heartbeat(
        job_id,
        execution_monitor=_Monitor(),
    )

    assert len(calls) == 1
    assert "updated_at" in calls[0][0]
    assert calls[0][1] is not None
    assert calls[0][2] is True


def test_maybe_auto_resume_skips_fresh_processing(monkeypatch):
    row = SimpleNamespace(
        id=9,
        export_id=2,
        status="PROCESSING",
        started_at=datetime.now(),
        updated_at=datetime.now(),
        created_at=datetime.now(),
    )

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.session_scope",
        lambda: _Db(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.crud_yolo_edge_validation_job.get",
        lambda db, job_id: row if job_id == 9 else None,
    )
    called = {"n": 0}

    def _fake_run(job_id: int) -> None:
        called["n"] += 1

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._run_manual_job_sync",
        _fake_run,
    )
    assert maybe_auto_resume_edge_validation_job(9) is False
    assert called["n"] == 0


def test_maybe_auto_resume_does_not_take_over_registered_board_agent_job(
    monkeypatch,
):
    now = datetime.now()
    row = SimpleNamespace(
        id=91,
        export_id=12,
        status="PROCESSING",
        edge_device_id="board-a",
        started_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=20),
        auto_training_round_id=None,
        threshold_source="manual",
    )

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.session_scope",
        lambda: _Db(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.crud_yolo_edge_validation_job.get",
        lambda db, job_id: row if job_id == 91 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.edge_device_registry.find_device_record",
        lambda _device_id: {
            "id": "board-a",
            "validation_channel": "board_agent",
        },
    )
    called = {"n": 0}
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._run_manual_job_sync",
        lambda _job_id: called.__setitem__("n", called["n"] + 1),
    )

    assert maybe_auto_resume_edge_validation_job(91, now=now) is False
    assert called["n"] == 0


def test_maybe_auto_resume_runs_strategy_evolution_pending(monkeypatch):
    row = SimpleNamespace(
        id=10,
        export_id=3,
        status="PENDING",
        threshold_source="strategy_evolution",
        started_at=None,
        updated_at=datetime.now(),
        created_at=datetime.now() - timedelta(minutes=1),
    )

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.session_scope",
        lambda: _Db(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.crud_yolo_edge_validation_job.get",
        lambda db, job_id: row if job_id == 10 else None,
    )
    called = {"job_ids": []}

    def _fake_thread(target=None, args=(), name=None, daemon=None):
        return SimpleNamespace(start=lambda: target(*args))

    def _fake_run(job_id: int) -> None:
        called["job_ids"].append(job_id)

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.threading.Thread",
        _fake_thread,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._run_manual_job_sync",
        _fake_run,
    )

    assert maybe_auto_resume_edge_validation_job(10) is True
    assert called["job_ids"] == [10]


def test_maybe_auto_resume_skips_generic_pending(monkeypatch):
    row = SimpleNamespace(
        id=11,
        export_id=3,
        status="PENDING",
        threshold_source="manual",
        started_at=None,
        updated_at=datetime.now(),
        created_at=datetime.now() - timedelta(minutes=1),
    )

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.session_scope",
        lambda: _Db(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.crud_yolo_edge_validation_job.get",
        lambda db, job_id: row if job_id == 11 else None,
    )
    called = {"n": 0}

    def _fake_run(job_id: int) -> None:
        called["n"] += 1

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._run_manual_job_sync",
        _fake_run,
    )

    assert maybe_auto_resume_edge_validation_job(11) is False
    assert called["n"] == 0


def test_maybe_auto_resume_skips_when_execution_lease_is_active(monkeypatch):
    now = datetime.now()
    row = SimpleNamespace(
        id=19,
        export_id=8,
        status="PROCESSING",
        threshold_source="manual",
        started_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=20),
        execution_lease_owner="edge-worker-a",
        execution_lease_expires_at=now + timedelta(minutes=5),
        auto_training_round_id=None,
    )

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.session_scope",
        lambda: _Db(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.crud_yolo_edge_validation_job.get",
        lambda _db, job_id: row if job_id == 19 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.is_stale_edge_validation_job",
        lambda *_args, **_kwargs: pytest.fail(
            "active execution lease must stop recovery first"
        ),
    )

    assert maybe_auto_resume_edge_validation_job(19, now=now) is False


def test_phase_one_gate_skips_automatic_edge_resume_but_keeps_manual(monkeypatch):
    now = datetime.now()
    rows = {
        21: SimpleNamespace(
            id=21,
            export_id=4,
            status="PROCESSING",
            auto_training_round_id=81,
            started_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            created_at=now - timedelta(minutes=20),
        ),
        22: SimpleNamespace(
            id=22,
            export_id=5,
            status="PROCESSING",
            auto_training_round_id=None,
            started_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            created_at=now - timedelta(minutes=20),
        ),
    }

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.session_scope",
        lambda: _Db(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.crud_yolo_edge_validation_job.get",
        lambda _db, job_id: rows.get(int(job_id)),
    )
    resumed: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._run_manual_job_sync",
        lambda job_id: resumed.append(int(job_id)),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_auto_resume.threading.Thread",
        lambda target, args, daemon, name: SimpleNamespace(
            start=lambda: target(*args)
        ),
    )

    assert maybe_auto_resume_edge_validation_job(21, now=now) is False
    assert maybe_auto_resume_edge_validation_job(22, now=now) is True
    assert resumed == [22]
