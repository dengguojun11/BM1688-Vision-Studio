from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from labelu.internal.application.command.edge_device_registry import (
    CreateEdgeDeviceCommand,
)
from labelu.internal.application.service import (
    edge_device_registry,
    yolo_edge_validation_scheduler as scheduler,
)
from labelu.internal.common import edge_device_registry as registry_io
from labelu.internal.common.config import settings


def _board_record(*, heartbeat_at: str | None = None) -> dict:
    return {
        "id": "board-agent-01",
        "name": "BM1688 板端 01",
        "enabled": True,
        "validation_channel": "board_agent",
        "host": "",
        "user": "",
        "password": "",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": heartbeat_at
        or datetime.now(timezone.utc).isoformat(),
        "board_agent_environment_ready": True,
        "board_agent_capabilities": {"bm1688": True},
    }


def test_board_agent_device_does_not_require_ssh_credentials():
    command = CreateEdgeDeviceCommand(
        name="板端主动拉取设备",
        validation_channel="board_agent",
    )

    assert command.host is None
    assert command.password is None

    with pytest.raises(ValidationError):
        CreateEdgeDeviceCommand(name="SSH 设备", validation_channel="ssh")


def test_ssh_device_with_private_key_only_is_a_configured_scheduler_candidate(
    tmp_path, monkeypatch
):
    from labelu.internal.common.crypto import encrypt_value

    key_path = tmp_path / "id_ed25519"
    key_path.write_text("test-private-key-material", encoding="utf-8")
    record = {
        "id": "ssh-key-only-01",
        "name": "SSH 私钥设备",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.54.169",
        "port": 22,
        "user": "linaro",
        "password_encrypted": "",
        "private_key_path_encrypted": encrypt_value(str(key_path)),
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "last_probe_status": "ok",
        "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "list_device_records",
        lambda: [record],
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "load_device_config",
        lambda _device_id: {
            "device_id": record["id"],
            "host": record["host"],
            "user": record["user"],
            "password": "",
            "private_key_path": str(key_path),
        },
    )

    configured = scheduler._configured_devices()

    assert [item["id"] for item in configured] == [record["id"]]
    assert scheduler._environment_is_ready(record) is True
    assert scheduler._device_is_online(record, {}) is True


def test_board_agent_only_device_is_configured_without_ssh_probe(monkeypatch):
    record = _board_record()
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(
        scheduler.edge_device_registry, "list_device_records", lambda: [record]
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "load_device_config",
        lambda _device_id: {"device_id": record["id"], "host": "", "password": ""},
    )

    configured = scheduler._configured_devices()
    assert [item["id"] for item in configured] == [record["id"]]
    assert scheduler._environment_is_ready(record) is True

    def fail_ssh_probe(_cfg):
        raise AssertionError("板端主动拉取设备不应调用 SSH 探测")

    monkeypatch.setattr(scheduler, "probe_edge_ssh_target", fail_ssh_probe)
    assert scheduler._device_is_online(record, {}) is True


def test_board_agent_heartbeat_expiry_is_not_online(monkeypatch):
    record = _board_record(
        heartbeat_at=(datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    )
    monkeypatch.setattr(settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120)
    assert scheduler._device_is_online(record, {}) is False


def test_failed_bootstrap_cannot_be_resurrected_by_ready_board_agent_heartbeat():
    """A failed enrollment remains unavailable despite a fresh board pulse."""

    record = {
        **_board_record(),
        "agent_bootstrap": {"status": "FAILED", "code": "install_failed"},
    }

    assert scheduler._environment_is_ready(record) is False

    view = edge_device_registry._device_to_view(record, active_device_id="")

    assert view.scheduling_status == "environment_not_ready"
    assert view.available_for_dispatch is False


def test_device_registry_view_requires_fresh_board_agent_heartbeat(monkeypatch):
    monkeypatch.setattr(settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120)
    stale = _board_record(
        heartbeat_at=(datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    )
    stale_view = edge_device_registry._device_to_view(
        stale,
        active_device_id="",
    )
    assert stale_view.scheduling_status == "offline"
    assert stale_view.available_for_dispatch is False

    fresh = _board_record(
        heartbeat_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    )
    fresh_view = edge_device_registry._device_to_view(
        fresh,
        active_device_id="",
    )
    assert fresh_view.scheduling_status == "idle"
    assert fresh_view.available_for_dispatch is True


def test_manual_board_agent_probe_uses_heartbeat_ttl(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120)
    stale = _board_record(
        heartbeat_at=(datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    )
    monkeypatch.setattr(
        edge_device_registry.registry_io,
        "find_device_record",
        lambda _device_id: stale,
    )
    result = edge_device_registry.probe_edge_device(stale["id"])
    assert result.status == "fail"
    assert result.code == "board_agent_offline"


def test_heartbeat_completes_waiting_bootstrap_without_ssh_secret(monkeypatch):
    record = {
        **_board_record(),
        "password": "",
        "password_encrypted": "",
        "agent_bootstrap": {
            "status": "WAITING_HEARTBEAT",
            "stage": "heartbeat",
            "code": "waiting",
        },
        "environment_report": {},
    }
    updates = {}

    monkeypatch.setattr(registry_io, "find_device_record", lambda _id: record)

    def update(_device_id, **changes):
        updates.update(changes)
        record.update(changes)
        return record

    monkeypatch.setattr(registry_io, "update_device_record", update)

    result = registry_io.record_board_agent_heartbeat(
        record["id"],
        environment_ready=True,
        capabilities={"bm1688": True},
    )

    assert result["board_agent_environment_ready"] is True
    assert updates["agent_bootstrap"]["status"] == "READY"
    assert updates["environment_status"] == "ready"
    assert updates["environment_report"]["agent_bootstrap"]["status"] == "READY"
