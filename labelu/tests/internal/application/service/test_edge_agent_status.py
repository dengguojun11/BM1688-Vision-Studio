from datetime import datetime, timedelta, timezone

from labelu.internal.application.service import edge_agent
from labelu.internal.common.config import settings
from labelu.internal.domain.models.edge_agent import EdgeAgent


def _row(*, status: str = "ONLINE", heartbeat=None) -> EdgeAgent:
    return EdgeAgent(
        agent_id="status-test",
        name="status-test",
        token_hash="0" * 64,
        status=status,
        last_heartbeat_at=heartbeat,
    )


def test_online_agent_with_fresh_heartbeat_stays_online(monkeypatch):
    monkeypatch.setattr(settings, "SEVEN_GAI_AGENT_OFFLINE_TTL_SECONDS", 60)
    response = edge_agent._agent_response(
        _row(heartbeat=datetime.now(timezone.utc) - timedelta(seconds=5))
    )
    assert response.status == "ONLINE"


def test_online_agent_with_expired_heartbeat_is_offline_in_read_model(monkeypatch):
    monkeypatch.setattr(settings, "SEVEN_GAI_AGENT_OFFLINE_TTL_SECONDS", 60)
    response = edge_agent._agent_response(
        _row(heartbeat=datetime.now(timezone.utc) - timedelta(seconds=61))
    )
    assert response.status == "OFFLINE"


def test_online_agent_with_future_heartbeat_is_offline_in_read_model(monkeypatch):
    monkeypatch.setattr(settings, "SEVEN_GAI_AGENT_OFFLINE_TTL_SECONDS", 60)
    now = datetime.now(timezone.utc)
    response = edge_agent._agent_response(
        _row(heartbeat=now + timedelta(hours=1))
    )
    assert response.status == "OFFLINE"


def test_missing_heartbeat_does_not_relabel_registered_agent():
    response = edge_agent._agent_response(_row(status="REGISTERED"))
    assert response.status == "REGISTERED"


def test_liveness_projection_does_not_override_revoked_state():
    response = edge_agent._agent_response(
        _row(
            status="REVOKED",
            heartbeat=datetime.now(timezone.utc) - timedelta(days=2),
        )
    )
    assert response.status == "REVOKED"


def test_monitor_read_model_is_server_paginated(db):
    db.add_all(
        [
            EdgeAgent(
                agent_id=f"page-{index}",
                name=f"page-{index}",
                project_id=1,
                token_hash="0" * 64,
                status="REGISTERED",
            )
            for index in range(3)
        ]
    )
    db.flush()

    rows, total = edge_agent.list_agents_page(db, 1, page=1, size=1)

    assert total == 3
    assert len(rows) == 1
    assert rows[0].project_id == 1


def test_monitor_read_model_searches_agent_identity_fields(db):
    db.add_all(
        [
            EdgeAgent(
                agent_id="camera-gate-01",
                name="东门摄像头",
                project_id=1,
                token_hash="0" * 64,
                status="REGISTERED",
                platform="BM1688",
                agent_version="2.4.1",
            ),
            EdgeAgent(
                agent_id="camera-yard-01",
                name="园区摄像头",
                project_id=1,
                token_hash="0" * 64,
                status="REGISTERED",
                platform="BM1688",
                agent_version="2.4.1",
            ),
        ]
    )
    db.flush()

    rows, total = edge_agent.list_agents_page(db, 1, page=0, size=20, search="东门")

    assert total == 1
    assert [row.agent_id for row in rows] == ["camera-gate-01"]
