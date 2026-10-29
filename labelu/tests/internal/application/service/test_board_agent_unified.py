from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from labelu.internal.application.service import board_agent
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


def _enable_unified(monkeypatch):
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_TOKEN", "board-static")
    monkeypatch.setattr(
        settings, "BOARD_AGENT_PUBLIC_BASE_URL", "http://labelu.example.test:8003"
    )
    monkeypatch.setattr(settings, "MEDIA_HOST", "")
    monkeypatch.setattr(settings, "BOARD_AGENT_TASK_TOKEN_SECRET", "task-secret")
    monkeypatch.setattr(settings, "BOARD_AGENT_TASK_TOKEN_TTL_SECONDS", 900)
    monkeypatch.setattr(
        board_agent.crud_yolo_bm1688_export,
        "get",
        lambda _db, _id: SimpleNamespace(
            id=int(_id),
            status="COMPLETED",
            imgsz=640,
            validation_thresholds_json=None,
            threshold_binding_json=None,
        ),
    )
    monkeypatch.setattr(
        board_agent.crud_yolo_train_job,
        "get",
        lambda _db, _id: SimpleNamespace(imgsz=640),
    )
    monkeypatch.setattr(
        board_agent, "_list_bmodel_names", lambda _id: ["model_int8.bmodel"]
    )


def test_board_agent_public_base_url_prefers_explicit_and_falls_back_to_media(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "BOARD_AGENT_PUBLIC_BASE_URL", "https://edge.example.test:8443/"
    )
    monkeypatch.setattr(settings, "MEDIA_HOST", "http://media.example.test:8003")
    assert board_agent._public_base_url() == "https://edge.example.test:8443"

    monkeypatch.setattr(settings, "BOARD_AGENT_PUBLIC_BASE_URL", "")
    assert board_agent._public_base_url() == "http://media.example.test:8003"


def test_board_agent_public_base_url_requires_explicit_reachable_config(monkeypatch):
    monkeypatch.setattr(settings, "BOARD_AGENT_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "MEDIA_HOST", "")
    with pytest.raises(LabelUException) as captured:
        board_agent._public_base_url()
    assert getattr(captured.value, "status_code", None) == 503
    assert captured.value.code == ErrorCode.CODE_30003_CLIENT_ERROR.value[0]
    assert "BOARD_AGENT_PUBLIC_BASE_URL" in str(captured.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:8003",
        "http://127.0.0.2:8003",
        "http://0.0.0.0:8003",
        "http://[::1]:8003",
        "http://[::]:8003",
        "http://[::ffff:127.0.0.1]:8003",
        "http://[not-an-ipv6-host:8003",
    ],
)
def test_board_agent_public_base_url_rejects_loopback_and_unspecified_hosts(
    monkeypatch, value
):
    monkeypatch.setattr(settings, "BOARD_AGENT_PUBLIC_BASE_URL", value)
    monkeypatch.setattr(settings, "MEDIA_HOST", "")
    with pytest.raises(LabelUException) as captured:
        board_agent._public_base_url()
    assert getattr(captured.value, "status_code", None) == 503
    assert captured.value.code == ErrorCode.CODE_30003_CLIENT_ERROR.value[0]
    assert "边端回连地址" in str(captured.value)


def test_unified_claim_rejects_unreachable_url_before_consuming_job(db, monkeypatch):
    _enable_unified(monkeypatch)
    monkeypatch.setattr(
        settings, "BOARD_AGENT_PUBLIC_BASE_URL", "http://127.0.0.1:8003"
    )
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=14,
        export_id=24,
        status="PROCESSING",
        edge_device_id="board-config-error",
        assigned_at=datetime.now(),
    )
    db.add(row)
    db.commit()

    with pytest.raises(LabelUException) as captured:
        board_agent.claim_next_unified_job(db, agent_id="board-config-error")
    assert getattr(captured.value, "status_code", None) == 503
    assert captured.value.code == ErrorCode.CODE_30003_CLIENT_ERROR.value[0]
    db.refresh(row)
    assert row.execution_lease_owner is None
    assert row.execution_fencing_token in (None, 0)


def test_unified_board_agent_claim_binds_device_and_execution_lease(db, monkeypatch):
    _enable_unified(monkeypatch)
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=11,
        export_id=21,
        status="PROCESSING",
        edge_device_id="board-a",
        assigned_at=datetime.now(),
    )
    db.add(row)
    db.commit()

    job = board_agent.claim_next_unified_job(db, agent_id="board-a")
    assert job is not None
    assert job.protocol_version == "unified-v1"
    assert job.agent_id == "board-a"
    assert job.task_token
    assert job.validation_id == row.id
    assert job.models_zip_url.startswith("http://labelu.example.test:8003/api/v1/")
    assert job.report_url.startswith("http://labelu.example.test:8003/api/v1/")
    assert job.progress_url.startswith("http://labelu.example.test:8003/api/v1/")
    db.refresh(row)
    assert row.execution_lease_owner == "board-agent:board-a"
    assert row.execution_fencing_token == 1

    assert board_agent.claim_next_unified_job(db, agent_id="board-other") is None


def test_unified_claim_rolls_back_candidate_read_before_device_lock(db, monkeypatch):
    """The registry lock must never be acquired with the candidate read open."""

    _enable_unified(monkeypatch)
    monkeypatch.setattr(
        board_agent.edge_device_registry,
        "find_device_record",
        lambda _agent_id: None,
    )
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=111,
        export_id=211,
        status="PROCESSING",
        edge_device_id="board-lock-order",
        assigned_at=datetime.now(),
    )
    db.add(row)
    db.commit()

    events = []
    original_rollback = db.rollback

    def tracked_rollback():
        events.append("rollback")
        return original_rollback()

    monkeypatch.setattr(db, "rollback", tracked_rollback)

    class _MutationGuard:
        def __enter__(self):
            events.append("lock")
            return True

        def __exit__(self, *_args):
            events.append("unlock")
            return False

    monkeypatch.setattr(
        board_agent.edge_device_registry,
        "device_mutation_lock",
        lambda: _MutationGuard(),
    )

    claimed = board_agent.claim_next_unified_job(db, agent_id="board-lock-order")

    assert claimed is not None
    assert events.index("rollback") < events.index("lock")


@pytest.mark.parametrize(
    "record_overrides",
    [
        {"agent_bootstrap": {"status": "FAILED", "code": "install_failed"}},
        {"board_agent_status": "offline"},
        {
            "board_agent_last_heartbeat_at": (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
        },
        {"board_agent_environment_ready": False},
    ],
)
def test_unified_claim_rechecks_registered_device_readiness(
    db, monkeypatch, record_overrides
):
    """A stale PROCESSING row must not bypass the device registry gate."""

    _enable_unified(monkeypatch)
    record = {
        "id": "board-gated",
        "enabled": True,
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
        "agent_bootstrap": {},
        **record_overrides,
    }
    monkeypatch.setattr(
        board_agent.edge_device_registry,
        "find_device_record",
        lambda _agent_id: record,
    )
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=15,
        export_id=25,
        status="PROCESSING",
        edge_device_id="board-gated",
        assigned_at=datetime.now(),
    )
    db.add(row)
    db.commit()

    assert board_agent.claim_next_unified_job(db, agent_id="board-gated") is None
    db.refresh(row)
    assert row.execution_lease_owner is None
    assert row.execution_fencing_token in (None, 0)


def test_legacy_claim_rejects_failed_registered_board_agent(db, monkeypatch):
    """The compatibility table must not bypass a durable failed bootstrap."""

    _enable_unified(monkeypatch)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    record = {
        "id": "board-legacy-failed",
        "enabled": True,
        "validation_channel": "board_agent",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
        "agent_bootstrap": {"status": "FAILED", "code": "install_failed"},
    }
    monkeypatch.setattr(
        board_agent.edge_device_registry,
        "find_device_record",
        lambda _agent_id: record,
    )
    calls = []
    monkeypatch.setattr(
        board_agent.crud_yolo_board_validation,
        "claim_next_pending",
        lambda _db: calls.append(True),
    )

    assert board_agent.claim_next_job(db, agent_id="board-legacy-failed") is None
    assert calls == []


def test_legacy_claim_keeps_unregistered_agent_compatibility(db, monkeypatch):
    """Old agents without a registry row can still use the legacy table."""

    _enable_unified(monkeypatch)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(
        board_agent.edge_device_registry,
        "find_device_record",
        lambda _agent_id: None,
    )
    calls = []
    monkeypatch.setattr(
        board_agent.crud_yolo_board_validation,
        "claim_next_pending",
        lambda _db: calls.append(True),
    )

    assert board_agent.claim_next_job(db, agent_id="old-board-agent") is None
    assert calls == [True]


def test_unified_board_agent_progress_refreshes_token_and_report_is_idempotent(
    db, monkeypatch
):
    _enable_unified(monkeypatch)
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=12,
        export_id=22,
        status="PROCESSING",
        edge_device_id="board-b",
        assigned_at=datetime.now(),
    )
    db.add(row)
    db.commit()
    claimed = board_agent.claim_next_unified_job(db, agent_id="board-b")
    assert claimed and claimed.task_token

    progress = board_agent.submit_unified_progress(
        db,
        validation_id=row.id,
        agent_id="board-b",
        task_token=claimed.task_token,
        stage="推理",
        message="正在运行三种精度模型",
        detail={"precision": "int8"},
    )
    assert progress.status == "PROCESSING"
    assert progress.task_token

    report = {"passed": True}
    first = board_agent.submit_unified_report(
        db,
        validation_id=row.id,
        agent_id="board-b",
        task_token=progress.task_token,
        status_value="COMPLETED",
        report=report,
        error_message=None,
    )
    assert first.status == "PARTIAL_COMPLETED"

    duplicate = board_agent.submit_unified_report(
        db,
        validation_id=row.id,
        agent_id="board-b",
        task_token=progress.task_token,
        status_value="COMPLETED",
        report=report,
        error_message=None,
    )
    assert duplicate.status == "PARTIAL_COMPLETED"

    with pytest.raises(Exception) as captured:
        board_agent.submit_unified_report(
            db,
            validation_id=row.id,
            agent_id="board-b",
            task_token=progress.task_token,
            status_value="COMPLETED",
            report={"passed": False},
            error_message="不同回报",
        )
    assert getattr(captured.value, "status_code", None) in {401, 409}


def test_unified_board_agent_expired_token_is_rejected(db, monkeypatch):
    _enable_unified(monkeypatch)
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=13,
        export_id=23,
        status="PROCESSING",
        edge_device_id="board-c",
        assigned_at=datetime.now(),
    )
    db.add(row)
    db.commit()
    claimed = board_agent.claim_next_unified_job(db, agent_id="board-c")
    assert claimed and claimed.task_token
    monkeypatch.setattr(
        board_agent.time, "time", lambda: datetime.now().timestamp() + 901
    )
    with pytest.raises(Exception) as captured:
        board_agent.submit_unified_progress(
            db,
            validation_id=row.id,
            agent_id="board-c",
            task_token=claimed.task_token,
            stage="下载",
            message="过期令牌",
        )
    assert getattr(captured.value, "status_code", None) == 401
