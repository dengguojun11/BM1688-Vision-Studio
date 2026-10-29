from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from labelu.internal.application.service import edge_device_registry as registry_service
from labelu.internal.common.config import settings


@pytest.fixture
def yolo_train_enabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)


def _sensitive_store() -> dict:
    return {
        "active_device_id": "edge-secret-1",
        "devices": [
            {
                "id": "edge-secret-1",
                "name": "测试边端",
                "enabled": True,
                "host": "10.20.30.40",
                "port": 2222,
                "user": "edge-admin",
                "password_encrypted": "ciphertext",
                "password_source_type": "mysql:encrypted",
                "password_masked_hint": "********7890",
                "password_updated_at": "2026-10-25T08:00:00+00:00",
                "edge_root": "/srv/labelu-edge",
                "remote_base": "/srv/labelu-edge/jobs",
                "sophon_demo_root": "/opt/sophon-demo",
                "validation_channel": "ssh",
                "board_agent_status": "online",
                "board_agent_last_heartbeat_at": "2026-10-25T08:10:00+00:00",
                "board_agent_environment_ready": True,
                "board_agent_capabilities": {"chip": "BM1688", "memory": "32G"},
                "board_agent_message": "remote diagnostic output",
                "agent_bootstrap": {
                    "status": "READY",
                    "stage": "WAITING_HEARTBEAT",
                    "code": "internal-agent-code",
                    "message": "private bootstrap detail",
                    "agent_version": "agent-9.9.9",
                    "updated_at": "2026-10-25T08:09:00+00:00",
                },
                "probe_status": "ok",
                "probe_stage": "remote_base",
                "probe_code": "probe-internal-code",
                "probe_message": "private probe detail",
                "last_probe_status": "ok",
                "last_probe_stage": "remote_base",
                "last_probe_code": "last-probe-code",
                "last_probe_message": "private last probe detail",
                "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                "environment_status": "ready",
                "environment_stage": "environment_verify",
                "environment_code": "environment-internal-code",
                "environment_message": "private environment detail",
                "environment_report": {
                    "environment_ready": True,
                    "remote_root": "/srv/labelu-edge",
                    "command_output": "private command output",
                },
            }
        ],
    }


def test_regular_user_gets_dispatch_projection_without_remote_secrets(
    client, testuser_token_headers, monkeypatch, yolo_train_enabled
):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "admin@123.com")
    monkeypatch.setattr(
        registry_service.registry_io, "load_device_store", _sensitive_store
    )
    monkeypatch.setattr(registry_service, "_busy_job_map", lambda _active: {})
    monkeypatch.setattr(
        registry_service.registry_io, "resolve_device_password", lambda _record: "secret"
    )

    response = client.get(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]["devices"][0]
    assert item["id"] == "edge-secret-1"
    assert item["scheduling_status"] == "idle"
    assert item["available_for_dispatch"] is True
    assert item["board_agent_status"] == "online"
    assert item["environment_status"] == "ready"

    for field in (
        "host",
        "port",
        "user",
        "edge_root",
        "remote_base",
        "sophon_demo_root",
        "password_source_type",
        "password_masked_hint",
        "password_configured",
        "password_runtime_available",
        "password_updated_at",
        "board_agent_capabilities",
        "board_agent_message",
        "environment_report",
        "agent_bootstrap_code",
        "agent_bootstrap_message",
        "agent_bootstrap_version",
        "agent_bootstrap_updated_at",
        "probe_code",
        "probe_message",
        "last_probe_code",
        "last_probe_message",
        "environment_code",
        "environment_message",
    ):
        assert field not in item, field

    assert item["agent_bootstrap_status"] == "READY"
    assert item["agent_bootstrap_stage"] == "WAITING_HEARTBEAT"

    detail_response = client.get(
        "/api/v1/edge-devices/edge-secret-1",
        headers=testuser_token_headers,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()["data"]
    assert detail["id"] == "edge-secret-1"
    assert detail["available_for_dispatch"] is True
    assert "host" not in detail
    assert "environment_report" not in detail
    assert "agent_bootstrap_message" not in detail


def test_system_admin_gets_complete_edge_device_detail(
    client, testadmin_token_headers, monkeypatch, yolo_train_enabled
):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "admin@123.com")
    monkeypatch.setattr(
        registry_service.registry_io, "load_device_store", _sensitive_store
    )
    monkeypatch.setattr(registry_service, "_busy_job_map", lambda _active: {})
    monkeypatch.setattr(
        registry_service.registry_io, "resolve_device_password", lambda _record: "secret"
    )

    response = client.get(
        "/api/v1/edge-devices/edge-secret-1",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]
    assert item["host"] == "10.20.30.40"
    assert item["port"] == 2222
    assert item["user"] == "edge-admin"
    assert item["edge_root"] == "/srv/labelu-edge"
    assert item["remote_base"] == "/srv/labelu-edge/jobs"
    assert item["password_configured"] is True
    assert item["password_masked_hint"] == "********7890"
    assert item["environment_report"]["command_output"] == "private command output"
    assert item["agent_bootstrap_message"] == "private bootstrap detail"

    list_response = client.get(
        "/api/v1/edge-devices",
        headers=testadmin_token_headers,
    )
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()["data"]["devices"][0]
    assert listed["host"] == "10.20.30.40"
    assert listed["environment_report"]["remote_root"] == "/srv/labelu-edge"


def test_stale_ssh_probe_is_not_dispatchable_in_device_list(
    client, testadmin_token_headers, monkeypatch, yolo_train_enabled
):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "admin@123.com")
    store = _sensitive_store()
    store["devices"][0]["last_probe_checked_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=121)
    ).isoformat()
    monkeypatch.setattr(
        registry_service.registry_io, "load_device_store", lambda: store
    )
    monkeypatch.setattr(registry_service, "_busy_job_map", lambda _active: {})
    monkeypatch.setattr(
        registry_service.registry_io, "resolve_device_password", lambda _record: "secret"
    )

    response = client.get(
        "/api/v1/edge-devices",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    item = response.json()["data"]["devices"][0]
    assert item["scheduling_status"] == "offline"
    assert item["available_for_dispatch"] is False

