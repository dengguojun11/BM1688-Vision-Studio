from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql

from labelu.internal.application.command.edge_queue_center import EdgeQueueListQuery
from labelu.internal.application.service import edge_queue_center as queue_center
from labelu.internal.application.service import yolo_edge_validation
from labelu.internal.application.service.edge_queue_timing import (
    profile_duration_medians,
    timing_for_row,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


def test_queue_ordering_is_mysql_compatible_without_native_nulls_last():
    statement = select(YoloEdgeValidationJob.id).order_by(
        *queue_center._nulls_last_order(YoloEdgeValidationJob.queued_at)
    )

    compiled = str(statement.compile(dialect=mysql.dialect()))

    assert "NULLS LAST" not in compiled.upper()
    assert "CASE WHEN" in compiled.upper()


def _user(db) -> User:
    user = db.query(User).filter(User.id == 1).first()
    if user is None:
        user = User(
            id=1,
            username="queue-test@example.com",
            hashed_password="test-hash",
        )
        db.add(user)
        db.flush()
    return user


def _task(db, user: User, *, name: str = "queue task") -> Task:
    task = Task(
        name=name,
        media_type="IMAGE",
        status="FINISHED",
        created_by=user.id,
    )
    db.add(task)
    db.flush()
    return task


def _job(
    db,
    user: User,
    task: Task,
    *,
    status: str = "PENDING",
    queued_at: datetime | None = None,
    source_type: str = "internal",
    source_system: str | None = None,
    pipeline_origin: str | None = None,
    device_id: str | None = None,
    queue_reason: str | None = "waiting_for_dispatch",
    export_id: int = 100,
) -> YoloEdgeValidationJob:
    row = YoloEdgeValidationJob(
        user_id=user.id,
        task_id=task.id,
        export_id=export_id,
        source_type=source_type,
        source_system=source_system,
        pipeline_origin=pipeline_origin,
        status=status,
        mode="run_only",
        sample_ids_json="[]",
        thresholds_json="{}",
        image_count=2,
        labeled_image_count=1,
        unlabeled_image_count=1,
        edge_device_id=device_id,
        edge_device_name=device_id,
        queue_reason=queue_reason,
        queued_at=queued_at,
        started_at=(queued_at if status == "PROCESSING" else None),
    )
    db.add(row)
    db.flush()
    return row


def test_queue_list_filters_and_preserves_global_position(db):
    user = _user(db)
    task = _task(db, user, name="camera batch")
    now = datetime.now()
    _job(db, user, task, queued_at=now - timedelta(minutes=5), export_id=101)
    second = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        source_type="third_party",
        device_id="edge-b",
        export_id=102,
    )
    third = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        device_id="edge-a",
        export_id=103,
    )
    db.commit()

    response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(
            device_id="edge-a", status="pending", active_only=True, size=20
        ),
    )

    assert response.total == 1
    assert [item.id for item in response.items] == [third.id]
    assert response.items[0].queue_position == 3
    assert response.items[0].waiting_seconds is not None
    assert response.items[0].display_status == response.items[0].status
    assert response.items[0].labeled_image_count == 1
    assert response.items[0].unlabeled_image_count == 1

    keyword_response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        keyword="camera",
        source_type="third_party",
    )
    assert keyword_response.total == 1
    assert keyword_response.items[0].id == second.id


def test_queue_source_filters_use_distinct_provenance_fields(db):
    user = _user(db)
    task = _task(db, user, name="source provenance task")
    now = datetime.now()
    auto_quality = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        source_type="auto_training",
        source_system="labelu_auto_training",
        pipeline_origin="AUTO_QUALITY",
        export_id=107,
    )
    manual_diagnostic = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=2),
        source_type="auto_training",
        source_system="labelu_auto_training",
        pipeline_origin="MANUAL_DIAGNOSTIC",
        export_id=108,
    )
    evolution = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=1),
        source_type="third_party",
        source_system="strategy_evolution",
        export_id=109,
    )
    db.commit()

    auto_response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(pipeline_origin="AUTO_QUALITY", size=20),
    )
    assert [item.id for item in auto_response.items] == [auto_quality.id]
    assert auto_response.items[0].pipeline_origin == "AUTO_QUALITY"
    assert auto_response.items[0].source_system == "labelu_auto_training"

    manual_response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(pipeline_origin="MANUAL_DIAGNOSTIC", size=20),
    )
    assert [item.id for item in manual_response.items] == [manual_diagnostic.id]
    assert manual_response.items[0].pipeline_origin == "MANUAL_DIAGNOSTIC"

    evolution_response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(source_system="strategy_evolution", size=20),
    )
    assert [item.id for item in evolution_response.items] == [evolution.id]
    assert evolution_response.items[0].source_system == "strategy_evolution"
    assert evolution_response.items[0].pipeline_origin is None


def test_bootstrap_in_progress_device_is_not_reported_ready():
    record = {
        "id": "edge-bootstrap",
        "enabled": True,
        "agent_bootstrap": {"status": "UPLOADING"},
        "last_probe_status": "ok",
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }

    online, ready, reason = queue_center._device_online_and_ready(record)

    assert online is False
    assert ready is False
    assert reason == "agent_bootstrap_in_progress"


def test_future_board_agent_heartbeat_is_not_reported_online_or_ready():
    record = {
        "id": "edge-future-heartbeat",
        "enabled": True,
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_environment_ready": True,
        "board_agent_last_heartbeat_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }

    online, ready, reason = queue_center._device_online_and_ready(record)

    assert online is False
    assert ready is False
    assert reason == "offline"


def test_stale_ssh_probe_is_not_reported_online_or_ready():
    record = {
        "id": "edge-stale-probe",
        "enabled": True,
        "last_probe_status": "ok",
        "last_probe_checked_at": (
            datetime.now(timezone.utc) - timedelta(seconds=121)
        ).isoformat(),
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }

    online, ready, reason = queue_center._device_online_and_ready(record)

    assert online is False
    assert ready is False
    assert reason == "offline"


def test_failed_auto_agent_bootstrap_is_not_reported_ready(monkeypatch):
    monkeypatch.setattr(
        queue_center.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True
    )
    monkeypatch.setattr(queue_center.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(queue_center.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(queue_center.settings, "BOARD_AGENT_TOKEN", "queue-agent-token")
    record = {
        "id": "edge-agent-failed",
        "enabled": True,
        "validation_channel": "ssh",
        "agent_bootstrap": {"status": "FAILED"},
        "last_probe_status": "ok",
        "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }

    online, ready, reason = queue_center._device_online_and_ready(record)

    assert online is False
    assert ready is False
    assert reason == "waiting_for_agent"


def test_failed_board_agent_bootstrap_is_not_reported_ready_with_fresh_heartbeat():
    record = {
        "id": "board-agent-failed",
        "enabled": True,
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
        "agent_bootstrap": {"status": "FAILED", "code": "install_failed"},
    }

    online, ready, reason = queue_center._device_online_and_ready(record)

    assert online is False
    assert ready is False
    assert reason == "waiting_for_agent"


def test_manual_board_agent_without_bootstrap_status_remains_ready():
    record = {
        "id": "board-agent-manual",
        "enabled": True,
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
    }

    online, ready, reason = queue_center._device_online_and_ready(record)

    assert online is True
    assert ready is True
    assert reason == "ready"


def test_queue_list_fifo_tie_break_matches_queue_positions(db):
    user = _user(db)
    task = _task(db, user, name="same timestamp queue")
    queued_at = datetime.now().replace(microsecond=0)
    first = _job(db, user, task, queued_at=queued_at, export_id=104)
    second = _job(db, user, task, queued_at=queued_at, export_id=105)
    db.commit()

    response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(status="PENDING", size=20),
    )

    assert [item.id for item in response.items] == [first.id, second.id]
    assert [item.queue_position for item in response.items] == [1, 2]


def test_legacy_list_and_detail_share_canonical_queue_positions(db, monkeypatch):
    """Legacy validation responses must match the task-center FIFO contract."""

    user = _user(db)
    task = _task(db, user, name="legacy queue contract")
    queued_at = datetime.now().replace(microsecond=0)
    first = _job(
        db,
        user,
        task,
        queued_at=queued_at,
        export_id=10_401,
    )
    preparing = _job(
        db,
        user,
        task,
        queued_at=queued_at + timedelta(seconds=1),
        queue_reason="preparing_pack",
        export_id=10_402,
    )
    # QUEUED is a valid persisted dispatch state.  The old scheduler helper
    # returned no position for it, while the task center treats it as part of
    # the same FIFO lane as PENDING.
    target = _job(
        db,
        user,
        task,
        status="QUEUED",
        queued_at=queued_at + timedelta(seconds=2),
        export_id=10_403,
    )
    target.thresholds_json = json.dumps(
        {"conf_thres": 0.25, "nms_thres": 0.7, "match_iou_thres": 0.5},
        ensure_ascii=False,
    )
    db.commit()

    center = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(size=20),
    )
    center_by_id = {item.id: item for item in center.items}

    legacy_rows, _meta = yolo_edge_validation.list_jobs(
        db,
        current_user=user,
        export_id=None,
        task_id=None,
        status_value=None,
        page=0,
        size=20,
    )
    legacy_by_id = {item.id: item for item in legacy_rows}

    assert center_by_id[first.id].queue_position == 1
    assert center_by_id[target.id].queue_position == 2
    assert center_by_id[preparing.id].queue_position is None
    assert (
        legacy_by_id[target.id].queue_position == center_by_id[target.id].queue_position
    )
    assert legacy_by_id[preparing.id].queue_position is None

    # Exercise the detail endpoint path as well, without requiring an export
    # fixture: access checking is orthogonal to the queue-rank contract.
    monkeypatch.setattr(
        yolo_edge_validation,
        "_load_job_with_access",
        lambda _db, *, job_id, current_user: (target, task),
    )
    detail = yolo_edge_validation.get_job(
        db,
        job_id=target.id,
        current_user=user,
    )
    assert detail.queue_position == center_by_id[target.id].queue_position


def test_queue_keyword_searches_job_task_and_device_identifiers(db):
    user = _user(db)
    task = _task(db, user, name="identifier search task")
    target = _job(
        db,
        user,
        task,
        queued_at=datetime.now(),
        device_id="edge-unique-id",
        export_id=106,
    )
    target.edge_device_name = "BM1688 unique board"
    db.commit()

    for keyword in (
        str(target.id),
        str(task.id),
        "edge-unique-id",
        "BM1688 unique board",
    ):
        response = queue_center.list_queue_jobs(
            db,
            current_user=user,
            query=EdgeQueueListQuery(keyword=keyword, size=20),
        )
        assert response.total == 1, keyword
        assert response.items[0].id == target.id, keyword


def test_queue_list_eta_uses_latest_completed_history(db):
    user = _user(db)
    task = _task(db, user, name="history task")
    now = datetime.now()

    # Insert older, short runs first so an unordered LIMIT 200 would prefer
    # them.  The 101 recent long runs must dominate the median after the
    # completed_at DESC ordering is applied.
    for index in range(100):
        completed_at = now - timedelta(days=2, seconds=index)
        row = _job(
            db,
            user,
            task,
            status="COMPLETED",
            export_id=10_000 + index,
        )
        row.started_at = completed_at - timedelta(seconds=10)
        row.completed_at = completed_at
    for index in range(101):
        completed_at = now - timedelta(minutes=index)
        row = _job(
            db,
            user,
            task,
            status="COMPLETED",
            export_id=11_000 + index,
        )
        row.started_at = completed_at - timedelta(seconds=1_000)
        row.completed_at = completed_at
    pending = _job(
        db,
        user,
        task,
        queued_at=now,
        export_id=12_000,
    )
    db.commit()

    response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(status="PENDING", size=20),
    )

    item = next(item for item in response.items if item.id == pending.id)
    assert item.estimated_seconds == 1_000


def test_running_eta_subtracts_elapsed_execution_time():
    started_at = datetime(2026, 1, 1, 12, 0, 0)
    now = started_at + timedelta(seconds=275)
    row = type(
        "RunningRow",
        (),
        {
            "status": "PROCESSING",
            "started_at": started_at,
            "result_json": json.dumps({"request": {"execution_profile": "quick"}}),
        },
    )()

    remaining = queue_center._estimate_for_row(
        row,
        median_duration=1_000,
        now=now,
    )

    assert remaining == 725


def test_terminal_timing_uses_updated_at_when_completed_at_is_missing():
    transition = datetime(2026, 1, 1, 0, 5, 0)
    row = SimpleNamespace(
        status="FAILED",
        queued_at=datetime(2026, 1, 1, 0, 0, 0),
        started_at=datetime(2026, 1, 1, 0, 2, 0),
        completed_at=None,
        updated_at=transition,
    )

    first = timing_for_row(row, current=transition + timedelta(hours=1))
    second = timing_for_row(row, current=transition + timedelta(hours=2))

    assert first == (120, 180)
    assert second == first


def test_eta_uses_profile_specific_history_medians():
    base = datetime(2026, 1, 1, 0, 0, 0)
    quick_history = SimpleNamespace(
        status="COMPLETED",
        started_at=base,
        completed_at=base + timedelta(seconds=100),
        result_json=json.dumps({"request": {"execution_profile": "quick"}}),
    )
    certification_history = SimpleNamespace(
        status="COMPLETED",
        started_at=base,
        completed_at=base + timedelta(seconds=400),
        result_json=json.dumps({"request": {"execution_profile": "certification"}}),
    )
    medians = profile_duration_medians([quick_history, certification_history])

    quick = SimpleNamespace(
        status="PENDING",
        result_json=json.dumps({"request": {"execution_profile": "quick"}}),
    )
    certification = SimpleNamespace(
        status="PENDING",
        result_json=json.dumps({"request": {"execution_profile": "certification"}}),
    )

    assert (
        queue_center._estimate_for_row(
            quick,
            median_duration=250,
            median_by_profile=medians,
            now=base,
        )
        == 100
    )
    assert (
        queue_center._estimate_for_row(
            certification,
            median_duration=250,
            median_by_profile=medians,
            now=base,
        )
        == 400
    )


def test_detail_and_queue_list_share_profile_eta(db):
    user = _user(db)
    task = _task(db, user, name="detail ETA contract")
    now = datetime.now()
    history = _job(
        db,
        user,
        task,
        status="COMPLETED",
        export_id=12_010,
    )
    history.started_at = now - timedelta(seconds=1_000)
    history.completed_at = now
    history.result_json = json.dumps(
        {"request": {"execution_profile": "quick"}}, ensure_ascii=False
    )
    active = _job(
        db,
        user,
        task,
        status="PROCESSING",
        queued_at=now - timedelta(seconds=20),
        export_id=12_011,
    )
    active.started_at = now - timedelta(seconds=10)
    active.result_json = json.dumps(
        {"request": {"execution_profile": "quick"}}, ensure_ascii=False
    )
    active.thresholds_json = json.dumps(
        {"conf_thres": 0.25, "nms_thres": 0.7, "match_iou_thres": 0.5},
        ensure_ascii=False,
    )
    db.commit()

    listed = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(status="PROCESSING", size=20),
    )
    listed_eta = listed.items[0].estimated_seconds
    detail = yolo_edge_validation._to_detail_response(
        active,
        task.name,
        db=db,
        current_user=user,
    )

    assert listed_eta is not None
    assert detail.estimated_seconds is not None
    assert abs(detail.estimated_seconds - listed_eta) <= 1


def test_history_entity_normalizes_direct_model_and_single_entity_row(db):
    user = _user(db)
    task = _task(db, user, name="history row shape")
    row = _job(db, user, task, status="COMPLETED", export_id=12_001)
    db.commit()

    assert queue_center._job_entity(row) is row
    assert queue_center._job_entity((row,)) is row


def test_queue_summary_has_counts_device_lanes_and_eta(db, monkeypatch):
    user = _user(db)
    task = _task(db, user)
    now = datetime.now()
    completed = _job(
        db,
        user,
        task,
        status="COMPLETED",
        queued_at=now - timedelta(hours=1),
        export_id=110,
    )
    completed.started_at = now - timedelta(minutes=20)
    completed.completed_at = now - timedelta(minutes=10)
    processing = _job(
        db,
        user,
        task,
        status="PROCESSING",
        queued_at=now - timedelta(minutes=8),
        device_id=None,
        queue_reason=None,
        export_id=111,
    )
    processing.started_at = now - timedelta(minutes=2)
    _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        export_id=112,
    )
    _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        queue_reason="waiting_for_environment_ready",
        export_id=113,
    )
    _job(db, user, task, status="FAILED", export_id=114)
    db.commit()

    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-a",
                "name": "Board A",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            },
            {
                "id": "edge-b",
                "name": "Board B",
                "enabled": True,
                "last_probe_status": "offline",
                "environment_status": "unknown",
                "environment_report": {},
            },
        ],
    )
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "get_active_device_id",
        lambda: "edge-a",
    )

    summary = queue_center.get_queue_summary(db, current_user=user)

    assert summary.counts.processing == 1
    assert summary.counts.queued == 1
    assert summary.counts.blocked == 1
    assert summary.counts.failed == 1
    assert summary.counts.completed == 1
    assert summary.counts.active == 2
    assert summary.counts.total == 5
    assert summary.oldest_wait_seconds is not None
    assert summary.estimated_clear_seconds is not None
    board_a = next(item for item in summary.devices if item.device_id == "edge-a")
    assert board_a.is_busy is True
    assert board_a.current_job_id == processing.id
    assert any(
        item.device_id == "edge-b" and not item.is_online for item in summary.devices
    )


def test_queue_summary_scopes_model_and_task_context_without_leaking_other_jobs(
    db, monkeypatch
):
    user = _user(db)
    task = _task(db, user, name="scoped model task")
    other_task = _task(db, user, name="other model task")
    now = datetime.now()
    selected = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        device_id="edge-a",
        export_id=501,
    )
    same_task_other_export = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        device_id="edge-a",
        export_id=502,
    )
    other_task_job = _job(
        db,
        user,
        other_task,
        queued_at=now - timedelta(minutes=5),
        device_id="edge-a",
        export_id=503,
    )
    selected_completed = _job(
        db,
        user,
        task,
        status="COMPLETED",
        export_id=504,
    )
    selected_completed.started_at = now - timedelta(minutes=2)
    selected_completed.completed_at = now - timedelta(minutes=1)
    other_completed = _job(
        db,
        user,
        other_task,
        status="COMPLETED",
        export_id=505,
    )
    other_completed.started_at = now - timedelta(minutes=4)
    other_completed.completed_at = now - timedelta(minutes=3)
    db.commit()

    fresh_probe = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-a",
                "name": "Scoped board",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": fresh_probe,
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            }
        ],
    )
    monkeypatch.setattr(
        queue_center.edge_device_registry, "get_active_device_id", lambda: ""
    )

    export_summary = queue_center.get_queue_summary(
        db,
        current_user=user,
        export_id=selected.export_id,
    )
    assert export_summary.counts.total == 1
    assert export_summary.counts.queued == 1
    assert export_summary.counts.completed == 0
    assert export_summary.oldest_queued_at == selected.queued_at
    assert export_summary.completed_duration_samples == 0
    export_board = export_summary.devices[0]
    assert export_board.next_job_id == selected.id
    assert export_board.pending_count == 1
    assert export_board.next_job_id not in {
        same_task_other_export.id,
        other_task_job.id,
    }

    task_summary = queue_center.get_queue_summary(
        db,
        current_user=user,
        task_id=task.id,
    )
    assert task_summary.counts.total == 3
    assert task_summary.counts.queued == 2
    assert task_summary.counts.completed == 1
    assert task_summary.completed_duration_samples == 1
    task_board = task_summary.devices[0]
    assert task_board.next_job_id == selected.id
    assert task_board.pending_count == 2
    assert task_board.next_job_id != other_task_job.id
    assert task_board.next_job_id != other_completed.id


def test_pack_preparing_job_is_counted_but_not_dispatch_preview(db, monkeypatch):
    user = _user(db)
    task = _task(db, user, name="pack task")
    now = datetime.now()
    preparing = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=2),
        queue_reason="preparing_pack",
        export_id=115,
    )
    ready = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=1),
        export_id=116,
    )
    db.commit()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-a",
                "name": "Board A",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            }
        ],
    )
    summary = queue_center.get_queue_summary(db, current_user=user)
    assert summary.counts.preparing == 1
    assert summary.counts.queued == 1
    board = summary.devices[0]
    assert board.next_job_id == ready.id
    assert board.next_job_id != preparing.id


def test_pack_preparing_job_makes_queue_eta_unknown(db, monkeypatch):
    """Preparation time is outside the execution-duration estimate contract."""

    user = _user(db)
    task = _task(db, user, name="pack eta task")
    preparing = _job(
        db,
        user,
        task,
        queued_at=datetime.now() - timedelta(minutes=1),
        queue_reason="preparing_pack",
        export_id=121,
    )
    db.commit()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-ready",
                "name": "在线设备",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            }
        ],
    )

    summary = queue_center.get_queue_summary(db, current_user=user)

    assert preparing.id not in {
        item.next_job_id for item in summary.devices if item.next_job_id
    }
    assert summary.counts.preparing == 1
    assert summary.estimated_clear_seconds is None
    assert summary.eta_confidence == "low"
    assert "准备" in summary.eta_basis


def test_pack_not_ready_and_legacy_whitespace_stage_are_not_fifo_heads(db, monkeypatch):
    user = _user(db)
    task = _task(db, user, name="legacy pack queue")
    now = datetime.now()
    not_ready = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        queue_reason="pack_not_ready",
        export_id=118,
    )
    legacy = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=2),
        queue_reason="waiting_for_dispatch",
        export_id=119,
    )
    legacy.progress_json = json.dumps(
        {"stage": "pack", "message": "still preparing"}, ensure_ascii=False
    )
    ready = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=1),
        export_id=120,
    )
    # A hand-edited legacy row can contain leading whitespace around the JSON.
    legacy.progress_json = "\n  " + legacy.progress_json
    db.commit()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-a",
                "name": "Board A",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            }
        ],
    )

    summary = queue_center.get_queue_summary(db, current_user=user)
    assert summary.counts.preparing == 2
    assert summary.counts.queued == 1

    board = summary.devices[0]
    assert board.next_job_id == ready.id
    assert board.next_job_id not in {not_ready.id, legacy.id}

    response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        # Derived rows are intentionally excluded from the PENDING filter;
        # inspect the full queue when validating their non-FIFO positions.
        query=EdgeQueueListQuery(size=20),
    )
    rows_by_id = {item.id: item for item in response.items}
    assert rows_by_id[not_ready.id].queue_position is None
    assert rows_by_id[legacy.id].queue_position is None
    assert rows_by_id[ready.id].queue_position == 1


def test_queue_status_filter_supports_derived_preparing_retrying_blocked_and_running(
    db,
):
    user = _user(db)
    task = _task(db, user, name="derived status filters")
    now = datetime.now()
    preparing = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        queue_reason="pack_not_ready",
        export_id=121,
    )
    retrying = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        queue_reason="retry_scheduled",
        export_id=122,
    )
    blocked = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        queue_reason="waiting_for_environment_ready",
        export_id=125,
    )
    waiting_for_pack = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=2, seconds=45),
        queue_reason="waiting_for_pack",
        export_id=127,
    )
    plain_queued = _job(
        db,
        user,
        task,
        status="QUEUED",
        queued_at=now - timedelta(minutes=2, seconds=30),
        export_id=126,
    )
    processing = _job(
        db,
        user,
        task,
        status="PROCESSING",
        queued_at=now - timedelta(minutes=2),
        export_id=123,
    )
    pending = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=1),
        export_id=124,
    )
    db.commit()

    def ids(status_value: str):
        response = queue_center.list_queue_jobs(
            db,
            current_user=user,
            query=EdgeQueueListQuery(status=status_value, size=20),
        )
        return {item.id for item in response.items}

    assert ids("PREPARING") == {preparing.id}
    assert ids("RETRYING") == {retrying.id}
    assert ids("BLOCKED") == {blocked.id, waiting_for_pack.id}
    assert ids("RUNNING") == {processing.id}
    # ``QUEUED`` is the plain dispatch lane.  Derived states remain persisted
    # as PENDING/QUEUED for scheduler compatibility, but must be returned only
    # by their corresponding operator-facing filter.
    assert ids("QUEUED") == {
        plain_queued.id,
        pending.id,
    }

    all_rows = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(size=20),
    )
    all_by_id = {item.id: item for item in all_rows.items}
    # A queue position belongs only to the plain dispatch lane. Derived
    # states remain visible in the list, but are waiting on a different gate
    # and must not make the FIFO look longer than it is.
    assert all_by_id[plain_queued.id].queue_position == 1
    assert all_by_id[pending.id].queue_position == 2
    for derived_id in (
        preparing.id,
        retrying.id,
        blocked.id,
        waiting_for_pack.id,
    ):
        assert all_by_id[derived_id].queue_position is None
    blocked_row = next(item for item in all_rows.items if item.id == blocked.id)
    assert blocked_row.status == "BLOCKED"


def test_pending_and_queued_filters_only_return_plain_dispatch_rows(db):
    """Persisted queue states must share one derived plain-lane filter."""

    user = _user(db)
    task = _task(db, user, name="pending queue filter consistency")
    now = datetime.now()
    plain = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        export_id=131,
    )
    preparing = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        queue_reason="pack_not_ready",
        export_id=132,
    )
    retrying = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=2),
        queue_reason="retry_scheduled",
        export_id=133,
    )
    blocked = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=1),
        queue_reason="waiting_for_environment_ready",
        export_id=134,
    )
    db.commit()

    for status_value in ("PENDING", "QUEUED"):
        response = queue_center.list_queue_jobs(
            db,
            current_user=user,
            query=EdgeQueueListQuery(status=status_value, size=20),
        )

        assert response.total == 1
        assert [item.id for item in response.items] == [plain.id]
        assert response.items[0].status == "PENDING"
        assert all(
            item.id not in {preparing.id, retrying.id, blocked.id}
            for item in response.items
        )


def test_execution_fencing_unavailable_is_blocked_in_summary_filter_and_row(
    db, monkeypatch
):
    """The fencing fail-closed requeue must remain visible as blocked work."""

    user = _user(db)
    task = _task(db, user, name="execution fencing blocked task")
    row = _job(
        db,
        user,
        task,
        queued_at=datetime.now() - timedelta(minutes=1),
        queue_reason="execution_fencing_unavailable",
        export_id=126,
    )
    db.commit()

    monkeypatch.setattr(
        queue_center.edge_device_registry, "list_device_records", lambda: []
    )

    summary = queue_center.get_queue_summary(db, current_user=user)
    assert summary.counts.blocked == 1

    response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(status="BLOCKED", size=20),
    )

    assert response.total == 1
    assert response.items[0].id == row.id
    assert response.items[0].status == "BLOCKED"


def test_derived_filters_and_summary_are_mutually_exclusive_for_legacy_pack_rows(
    db, monkeypatch
):
    user = _user(db)
    task = _task(db, user, name="derived state consistency")
    now = datetime.now()
    plain = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=9),
        queue_reason="waiting_for_dispatch",
        export_id=201,
    )
    preparing = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=8),
        queue_reason="waiting_for_dispatch",
        export_id=202,
    )
    preparing.result_json = json.dumps(
        {"request": {}, "pack": {"state": "PREPARING"}}, ensure_ascii=False
    )
    retrying = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=7),
        queue_reason="retry_scheduled",
        export_id=203,
    )
    retrying_preparing = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=6),
        queue_reason="retry_scheduled",
        export_id=204,
    )
    retrying_preparing.result_json = json.dumps(
        {"pack": {"state": "UPLOADING"}}, ensure_ascii=False
    )
    blocked = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=5),
        queue_reason="waiting_for_environment_ready",
        export_id=205,
    )
    blocked_preparing = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        queue_reason="waiting_for_environment_ready",
        export_id=206,
    )
    blocked_preparing.result_json = json.dumps(
        {"pack": {"state": "PREPARING"}}, ensure_ascii=False
    )
    processing = _job(
        db,
        user,
        task,
        status="PROCESSING",
        queued_at=now - timedelta(minutes=3),
        queue_reason=None,
        export_id=207,
    )
    failed = _job(db, user, task, status="FAILED", export_id=208)
    completed = _job(db, user, task, status="COMPLETED", export_id=209)
    db.commit()

    monkeypatch.setattr(
        queue_center.edge_device_registry, "list_device_records", lambda: []
    )
    monkeypatch.setattr(
        queue_center.edge_device_registry, "get_active_device_id", lambda: ""
    )

    summary = queue_center.get_queue_summary(db, current_user=user)
    assert summary.counts.queued == 1
    assert summary.counts.preparing == 2
    assert summary.counts.retrying == 1
    assert summary.counts.blocked == 2
    assert summary.counts.processing == 1
    assert summary.counts.failed == 1
    assert summary.counts.completed == 1
    assert summary.counts.active == 5
    assert summary.counts.total == 9

    def filtered(status_value: str):
        return queue_center.list_queue_jobs(
            db,
            current_user=user,
            query=EdgeQueueListQuery(status=status_value, size=20),
        )

    assert filtered("QUEUED").total == summary.counts.queued
    assert filtered("PREPARING").total == summary.counts.preparing
    assert filtered("RETRYING").total == summary.counts.retrying
    assert filtered("BLOCKED").total == summary.counts.blocked
    assert filtered("RUNNING").total == summary.counts.processing

    all_rows = queue_center.list_queue_jobs(
        db, current_user=user, query=EdgeQueueListQuery(size=20)
    )
    assert all_rows.total == summary.counts.total
    display_by_id = {item.id: item.status for item in all_rows.items}
    assert display_by_id[plain.id] == "PENDING"
    assert display_by_id[preparing.id] == "PREPARING"
    assert display_by_id[retrying.id] == "RETRYING"
    assert display_by_id[retrying_preparing.id] == "PREPARING"
    assert display_by_id[blocked.id] == "BLOCKED"
    assert display_by_id[blocked_preparing.id] == "BLOCKED"
    assert display_by_id[processing.id] == "RUNNING"


def test_dispatch_previews_and_oldest_wait_exclude_blocked_and_retrying_rows(
    db, monkeypatch
):
    """Device previews, FIFO ranks, and oldest-wait use one dispatch lane."""

    user = _user(db)
    task = _task(db, user, name="dispatch lane projection")
    now = datetime.now()
    _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=5),
        device_id="edge-a",
        queue_reason="waiting_for_environment_ready",
        export_id=301,
    )
    _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=4),
        device_id="edge-a",
        queue_reason="retry_scheduled",
        export_id=302,
    )
    _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=3),
        queue_reason="waiting_for_online_device",
        export_id=303,
    )
    _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=2),
        queue_reason="retrying",
        export_id=304,
    )
    plain = _job(
        db,
        user,
        task,
        queued_at=now - timedelta(minutes=1),
        export_id=305,
    )
    db.commit()

    fresh_probe = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-a",
                "name": "Edge A",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": fresh_probe,
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            },
            {
                "id": "edge-b",
                "name": "Edge B",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": fresh_probe,
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            },
        ],
    )
    monkeypatch.setattr(
        queue_center.edge_device_registry, "get_active_device_id", lambda: ""
    )

    summary = queue_center.get_queue_summary(db, current_user=user)

    assert summary.counts.queued == 1
    assert summary.counts.retrying == 2
    assert summary.counts.blocked == 2
    assert summary.counts.active == 3
    assert summary.oldest_queued_at == plain.queued_at

    devices = {item.device_id: item for item in summary.devices}
    next_ids = {
        item.next_job_id for item in devices.values() if item.next_job_id is not None
    }
    assert next_ids == {plain.id}
    assert all(
        item.next_queue_position == 1 for item in devices.values() if item.next_job_id
    )
    assert sum(int(item.pending_count or 0) for item in devices.values()) == 1

    response = queue_center.list_queue_jobs(
        db,
        current_user=user,
        query=EdgeQueueListQuery(size=20),
    )
    row = next(item for item in response.items if item.id == plain.id)
    assert row.queue_position == 1


def test_summary_does_not_claim_zero_eta_for_processing_without_ready_device(
    db, monkeypatch
):
    user = _user(db)
    task = _task(db, user, name="stale device task")
    processing = _job(
        db,
        user,
        task,
        status="PROCESSING",
        device_id="edge-stale",
        queued_at=datetime.now() - timedelta(minutes=2),
        queue_reason=None,
        export_id=116,
    )
    processing.started_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-stale",
                "name": "离线设备",
                "enabled": True,
                "last_probe_status": "offline",
                "environment_status": "unknown",
                "environment_report": {},
            }
        ],
    )
    summary = queue_center.get_queue_summary(db, current_user=user)
    assert processing.id in {
        item.current_job_id for item in summary.devices if item.current_job_id
    }
    assert summary.estimated_clear_seconds is None


def test_summary_eta_is_unknown_when_mixed_ready_and_offline_lanes_have_work(
    db, monkeypatch
):
    user = _user(db)
    task = _task(db, user, name="mixed device eta")
    now = datetime.now()
    processing = _job(
        db,
        user,
        task,
        status="PROCESSING",
        device_id="edge-offline",
        queued_at=now - timedelta(minutes=2),
        queue_reason=None,
        export_id=118,
    )
    processing.started_at = now - timedelta(minutes=1)
    pending = _job(
        db,
        user,
        task,
        device_id="edge-offline",
        queued_at=now - timedelta(seconds=30),
        export_id=119,
    )
    db.commit()
    monkeypatch.setattr(
        queue_center.edge_device_registry,
        "list_device_records",
        lambda: [
            {
                "id": "edge-ready",
                "name": "在线设备",
                "enabled": True,
                "last_probe_status": "ok",
                "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "environment_status": "ready",
                "environment_report": {"environment_ready": True},
            },
            {
                "id": "edge-offline",
                "name": "离线设备",
                "enabled": True,
                "last_probe_status": "offline",
                "environment_status": "unknown",
                "environment_report": {},
            },
        ],
    )

    summary = queue_center.get_queue_summary(db, current_user=user)

    assert summary.estimated_clear_seconds is None
    assert summary.eta_confidence == "low"
    assert "离线" in summary.eta_basis
    offline = next(item for item in summary.devices if item.device_id == "edge-offline")
    ready = next(item for item in summary.devices if item.device_id == "edge-ready")
    assert offline.current_job_id == processing.id
    assert offline.pending_count == 1
    assert offline.eta_seconds is None
    assert ready.eta_seconds == 0
    assert pending.id != processing.id


def test_retry_rejects_explicitly_broken_pack(db):
    user = _user(db)
    task = _task(db, user)
    failed = _job(db, user, task, status="FAILED", export_id=117)
    failed.result_json = json.dumps(
        {"request": {}, "pack": {"state": "FAILED"}}, ensure_ascii=False
    )
    db.commit()
    with pytest.raises(Exception) as exc_info:
        queue_center.retry_job(db, job_id=failed.id, current_user=user)
    assert "样本包不可用" in str(exc_info.value)
    db.expire_all()
    assert db.get(YoloEdgeValidationJob, failed.id).status == "FAILED"


def test_retry_rejects_automatic_job_when_plan_writes_are_closed(db, monkeypatch):
    user = _user(db)
    task = _task(db, user, name="closed automatic retry")
    failed = _job(db, user, task, status="FAILED", export_id=123)
    failed.auto_training_plan_id = 77
    failed.auto_training_round_id = 88
    db.commit()
    monkeypatch.setattr(
        queue_center.settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False
    )

    with pytest.raises(Exception) as exc_info:
        queue_center.retry_job(db, job_id=failed.id, current_user=user)

    assert exc_info.value.status_code == 403
    assert getattr(exc_info.value, "code", None) == 57122
    db.expire_all()
    assert db.get(YoloEdgeValidationJob, failed.id).status == "FAILED"


def test_queue_projects_missing_ready_pack_as_blocked_without_fifo_position(
    db, tmp_path
):
    user = _user(db)
    task = _task(db, user, name="missing ready pack")
    row = _job(
        db,
        user,
        task,
        queued_at=datetime.now() - timedelta(minutes=1),
        export_id=124,
    )
    row.result_json = json.dumps(
        {
            "pack": {
                "state": "READY",
                "manifest_path": str(tmp_path / "missing-manifest.json"),
            }
        },
        ensure_ascii=False,
    )
    db.commit()

    response = queue_center.list_queue_jobs(
        db, current_user=user, query=EdgeQueueListQuery(size=20)
    )

    item = next(item for item in response.items if item.id == row.id)
    assert item.status == "BLOCKED"
    assert item.display_status == "BLOCKED"
    assert item.queue_reason == "waiting_for_pack"
    assert item.queue_position is None


def test_retry_rechecks_pack_after_row_lock(db, monkeypatch, tmp_path):
    user = _user(db)
    task = _task(db, user, name="retry pack race")
    failed = _job(db, user, task, status="FAILED", export_id=120)
    failed.result_json = json.dumps(
        {
            "request": {},
            "pack": {
                "state": "READY",
                "manifest_path": str(tmp_path / "manifest.json"),
            },
        },
        ensure_ascii=False,
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"blocking_skipped_count": 0}), encoding="utf-8"
    )
    db.commit()
    checks: list[int] = []

    def pack_check(_row):
        checks.append(1)
        return len(checks) == 1

    monkeypatch.setattr(queue_center, "_explicit_pack_is_retryable", pack_check)

    with pytest.raises(Exception) as exc_info:
        queue_center.retry_job(db, job_id=failed.id, current_user=user)

    assert "样本包不可用" in str(exc_info.value)
    assert len(checks) == 2
    db.expire_all()
    assert db.get(YoloEdgeValidationJob, failed.id).status == "FAILED"


def test_cancel_and_retry_use_terminal_state_contract(db, monkeypatch, tmp_path):
    user = _user(db)
    task = _task(db, user)
    queued = _job(db, user, task, queued_at=datetime.now(), export_id=120)
    queued.started_at = datetime.now() - timedelta(minutes=1)
    queued.queue_lease_owner = "dispatch-owner"
    queued.queue_lease_expires_at = datetime.now() + timedelta(minutes=5)
    queued.execution_lease_owner = "execution-owner"
    queued.execution_lease_expires_at = datetime.now() + timedelta(minutes=5)
    failed = _job(db, user, task, status="FAILED", export_id=121)
    failed.result_json = json.dumps(
        {
            "request": {"execution_profile": "certification"},
            "pack": {
                "state": "READY",
                "manifest_path": str(tmp_path / "manifest.json"),
            },
        },
        ensure_ascii=False,
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"blocking_skipped_count": 0}), encoding="utf-8"
    )
    processing = _job(
        db,
        user,
        task,
        status="PROCESSING",
        device_id="edge-a",
        queued_at=datetime.now(),
        export_id=122,
    )
    db.commit()

    cancelled = queue_center.cancel_job(
        db, job_id=queued.id, current_user=user, reason="用户取消"
    )
    db.expire_all()
    assert cancelled.status == "CANCELLED"
    cancelled_row = db.get(YoloEdgeValidationJob, queued.id)
    assert cancelled_row.status == "CANCELLED"
    assert cancelled_row.edge_device_id is None
    assert cancelled_row.started_at is None
    assert cancelled_row.queue_lease_owner is None
    assert cancelled_row.queue_lease_expires_at is None
    assert cancelled_row.execution_lease_owner is None
    assert cancelled_row.execution_lease_expires_at is None

    enqueued: list[int] = []
    monkeypatch.setattr(
        queue_center.yolo_edge_validation_scheduler,
        "enqueue_edge_validation_job",
        lambda job_id: enqueued.append(int(job_id)) or True,
    )
    monkeypatch.setattr(
        queue_center.yolo_edge_validation_scheduler,
        "schedule_edge_validation_dispatch",
        lambda: None,
    )
    retried = queue_center.retry_job(
        db, job_id=failed.id, current_user=user, reason="修复运行时"
    )
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, failed.id)
    assert retried.status == "PENDING"
    assert refreshed.status == "PENDING"
    assert refreshed.result_json is not None
    assert refreshed.queue_reason == "retry_scheduled"
    assert refreshed.execution_lease_owner is None
    assert refreshed.execution_lease_expires_at is None
    assert enqueued == [failed.id]

    with pytest.raises(Exception) as exc_info:
        queue_center.cancel_job(db, job_id=processing.id, current_user=user)
    assert "正在边端执行" in str(exc_info.value)


def test_queue_summary_http_contract(client, testadmin_token_headers):
    response = client.get(
        "/api/v1/yolo-edge-validation/queue/summary",
        headers=testadmin_token_headers,
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert set(("counts", "devices", "estimated_clear_seconds")).issubset(payload)

    scoped_response = client.get(
        "/api/v1/yolo-edge-validation/queue/summary",
        params={"export_id": 1, "task_id": 1},
        headers=testadmin_token_headers,
    )
    assert scoped_response.status_code == 200
    scoped_payload = scoped_response.json()["data"]
    assert set(("counts", "devices", "estimated_clear_seconds")).issubset(
        scoped_payload
    )
