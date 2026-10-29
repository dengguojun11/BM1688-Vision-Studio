from __future__ import annotations

from datetime import datetime, timezone

from labelu.internal.adapter.persistence import crud_yolo_board_validation
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction


def test_board_agent_internal_route_uses_token_instead_of_retirement_gate(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(settings, "BOARD_AGENT_TOKEN", "board-test-token")
    monkeypatch.setattr(
        "labelu.internal.application.service.board_agent.claim_next_job",
        lambda db: None,
    )

    unauthorized = client.get("/api/v1/board-agent/jobs/next")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/api/v1/board-agent/jobs/next",
        headers={"X-Board-Agent-Token": "board-test-token"},
    )
    assert authorized.status_code == 200
    assert authorized.json() == {"data": None}


def test_legacy_board_agent_route_forwards_registered_identity(client, monkeypatch):
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(settings, "BOARD_AGENT_TOKEN", "board-test-token")
    calls = []

    def claim(db, *, agent_id=None):
        calls.append(agent_id)
        return None

    monkeypatch.setattr(
        "labelu.internal.application.service.board_agent.claim_next_job", claim
    )

    response = client.get(
        "/api/v1/board-agent/jobs/next",
        headers={
            "X-Board-Agent-Token": "board-test-token",
            "X-Board-Agent-Id": "board-legacy-01",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"data": None}
    assert calls == ["board-legacy-01"]


def test_legacy_route_does_not_claim_failed_registered_agent_job(
    client, db, monkeypatch
):
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(settings, "BOARD_AGENT_TOKEN", "board-test-token")
    monkeypatch.setattr(
        settings, "BOARD_AGENT_PUBLIC_BASE_URL", "http://labelu.example.test:8003"
    )
    monkeypatch.setattr(settings, "MEDIA_HOST", "")
    monkeypatch.setattr(
        "labelu.internal.application.service.board_agent.edge_device_registry.find_device_record",
        lambda _agent_id: {
            "id": "board-legacy-failed",
            "enabled": True,
            "validation_channel": "board_agent",
            "board_agent_status": "online",
            "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "board_agent_environment_ready": True,
            "agent_bootstrap": {"status": "FAILED", "code": "install_failed"},
        },
    )
    with begin_transaction(db):
        row = crud_yolo_board_validation.create_for_export(
            db,
            export_id=900002,
            task_id=900002,
            yolo_train_job_id=900002,
        )
        validation_id = row.id

    response = client.get(
        "/api/v1/board-agent/jobs/next",
        headers={
            "X-Board-Agent-Token": "board-test-token",
            "X-Board-Agent-Id": "board-legacy-failed",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"data": None}
    persisted = crud_yolo_board_validation.get(db, validation_id)
    assert persisted is not None
    assert persisted.status == "PENDING"


def test_board_agent_route_stays_internal_but_user_validation_route_is_documented(
    client,
):
    paths = client.app.openapi()["paths"]
    assert not [path for path in paths if path.startswith("/api/v1/board-agent")]
    validation_path = "/api/v1/yolo-bm1688-exports/{export_id}/board-validation"
    assert {"get", "post"} <= set(paths[validation_path])


def test_claim_next_pending_does_not_return_one_job_twice(db):
    with begin_transaction(db):
        row = crud_yolo_board_validation.create_for_export(
            db,
            export_id=900001,
            task_id=900001,
            yolo_train_job_id=900001,
        )
        validation_id = row.id

    with begin_transaction(db):
        first = crud_yolo_board_validation.claim_next_pending(db)
    with begin_transaction(db):
        second = crud_yolo_board_validation.claim_next_pending(db)

    assert first is not None
    assert first.id == validation_id
    assert first.status == "PROCESSING"
    assert first.claimed_at is not None
    assert second is None
