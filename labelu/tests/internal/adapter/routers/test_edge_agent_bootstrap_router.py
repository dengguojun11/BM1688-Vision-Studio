from __future__ import annotations

from labelu.internal.adapter.routers import yolo_train as yolo_train_router
from labelu.internal.application.response.edge_agent_bootstrap import (
    EdgeAgentBootstrapResponse,
)


def _bootstrap_response(device_id: str) -> EdgeAgentBootstrapResponse:
    return EdgeAgentBootstrapResponse(
        device_id=device_id,
        status="QUEUED",
        stage="QUEUED",
        code="queued",
        message="边端 Agent 引导已排队",
    )


def test_admin_can_queue_edge_agent_bootstrap(
    client, testadmin_token_headers, monkeypatch
):
    calls = []

    def start(cmd):
        calls.append(cmd)
        return _bootstrap_response(cmd.device_id)

    monkeypatch.setattr(
        yolo_train_router.edge_agent_bootstrap_service,
        "start_bootstrap_background",
        start,
    )

    response = client.post(
        "/api/v1/edge-devices/agent-bootstrap",
        headers=testadmin_token_headers,
        json={"device_id": "edge-http-1"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["data"]["status"] == "QUEUED"
    assert [item.device_id for item in calls] == ["edge-http-1"]


def test_edge_agent_bootstrap_rejects_path_body_device_mismatch(
    client, testadmin_token_headers, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        yolo_train_router.edge_agent_bootstrap_service,
        "start_bootstrap_background",
        lambda cmd: calls.append(cmd),
    )

    response = client.post(
        "/api/v1/edge-devices/edge-http-1/agent-bootstrap",
        headers=testadmin_token_headers,
        json={"device_id": "edge-http-2"},
    )

    assert response.status_code == 400, response.text
    assert "device_id" in response.text
    assert calls == []


def test_admin_can_read_edge_agent_bootstrap_status(
    client, testadmin_token_headers, monkeypatch
):
    expected = _bootstrap_response("edge-http-3")
    monkeypatch.setattr(
        yolo_train_router.edge_agent_bootstrap_service,
        "get_bootstrap_status",
        lambda device_id: expected,
    )

    response = client.get(
        "/api/v1/edge-devices/edge-http-3/agent-bootstrap",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["device_id"] == "edge-http-3"
    assert response.json()["data"]["status"] == "QUEUED"
