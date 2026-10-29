import json

import pytest


def test_edge_device_registry_crud_and_active_config():
    from labelu.internal.common.edge_device_registry import (
        create_device_record,
        delete_device_record,
        load_active_device_config,
        load_device_store,
        set_active_device_id,
        update_device_record,
    )

    rec1 = create_device_record(
        name="edge-box-01",
        host="10.10.11.26",
        user="linaro",
        password="secret-1111",
        set_active=True,
    )
    rec2 = create_device_record(
        name="edge-box-02",
        host="10.10.11.27",
        user="linaro",
        password="secret-2222",
        set_active=False,
    )

    store = load_device_store()
    assert store["active_device_id"] == rec1["id"]
    assert len(store["devices"]) == 2
    assert all(item["password_source_type"] == "mysql:encrypted" for item in store["devices"])

    set_active_device_id(rec2["id"])
    update_device_record(rec2["id"], remote_base="/data/edge-box-02")

    active = load_active_device_config()
    assert active["host"] == "10.10.11.27"
    assert active["password"] == "secret-2222"
    assert active["edge_root"] == "/data/labelu_edge"
    assert active["remote_base"] == "/data/labelu_edge/jobs"
    assert active["sophon_demo_root"].startswith("/data/labelu_edge/")

    payload = json.dumps(load_device_store(), ensure_ascii=False)
    assert "secret-1111" not in payload
    assert "secret-2222" not in payload

    delete_device_record(rec1["id"])
    assert [item["id"] for item in load_device_store()["devices"]] == [rec2["id"]]


def test_edge_device_registry_does_not_import_legacy_json_or_environment():
    from labelu.internal.common.edge_device_registry import load_active_device_config, load_device_store

    store = load_device_store()
    assert store == {"version": 1, "active_device_id": "", "devices": []}
    active = load_active_device_config()
    assert active["enabled"] is False
    assert active["host"] == ""
    assert active["password"] == ""


def test_edge_device_registry_auto_names_device_from_host():
    from labelu.internal.common.edge_device_registry import create_device_record

    record = create_device_record(
        name=None,
        host="192.168.54.111",
        user="linaro",
        password="page-secret-1818",
        set_active=True,
    )
    assert record["name"] == "192.168.54.111"
    assert record["password_source_type"] == "mysql:encrypted"
    assert record["password_is_configured"] is True
    assert record["password_masked_hint"].endswith("1818")


def test_switching_board_agent_to_ssh_requires_a_new_password():
    from labelu.internal.common.edge_device_registry import (
        create_device_record,
        update_device_record,
    )

    record = create_device_record(
        name="板端主动拉取设备",
        validation_channel="board_agent",
        host=None,
        user="",
        password=None,
        set_active=True,
    )
    with pytest.raises(ValueError, match="SSH 通道必须配置 SSH 密码"):
        update_device_record(record["id"], validation_channel="ssh")


def test_board_agent_bootstrap_transition_keeps_ssh_secret_until_ready(monkeypatch):
    """A failed/awaiting install must retain SSH credentials for retry."""

    from labelu.internal.common import edge_device_registry as registry
    from labelu.internal.common.crypto import encrypt_value

    ciphertext = encrypt_value("bootstrap-secret")
    base_record = {
        "id": "edge_bootstrap_secret",
        "name": "bootstrap-secret-device",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.54.169",
        "port": 22,
        "user": "linaro",
        "password_encrypted": ciphertext,
    }
    store = {
        "version": 1,
        "active_device_id": base_record["id"],
        "devices": [dict(base_record)],
    }

    monkeypatch.setattr(registry, "load_device_store", lambda: store)

    # ``update_device_record`` mutates the loaded store before writing it;
    # keeping the in-memory adapter as a no-op lets each call observe that
    # updated snapshot without aliasing/clearing the same object.
    monkeypatch.setattr(registry, "_write_device_store", lambda _value: None)

    # Public channel switching is terminal for the SSH credential.
    switched = registry.update_device_record(
        base_record["id"], validation_channel="board_agent"
    )
    assert switched.get("password_encrypted", "") == ""
    assert switched["password_is_configured"] is False

    # The bootstrap worker may switch the channel before the first heartbeat;
    # preserve the encrypted secret so a timeout can retry over SSH.
    store["devices"] = [dict(base_record)]
    transitioning = registry.update_device_record(
        base_record["id"],
        validation_channel="board_agent",
        preserve_password_on_board_agent=True,
        board_agent_status="offline",
    )
    assert transitioning["password_encrypted"] == ciphertext
    assert transitioning["password_is_configured"] is True

    # Repeated lifecycle writes omit the channel and must not clear it either.
    waiting = registry.update_device_record(
        base_record["id"],
        board_agent_status="offline",
        environment_code="heartbeat_timeout",
    )
    assert waiting["password_encrypted"] == ciphertext

    # Once a real heartbeat has made the device READY, the service explicitly
    # clears the no-longer-needed SSH secret.
    ready = registry.update_device_record(
        base_record["id"],
        validation_channel="board_agent",
        password_secret_action="clear",
        board_agent_status="online",
        board_agent_environment_ready=True,
    )
    assert ready.get("password_encrypted", "") == ""
    assert ready["password_is_configured"] is False


def test_existing_ciphertext_is_preserved_and_legacy_env_fields_are_removed():
    from labelu.internal.common.crypto import encrypt_value
    from labelu.internal.common.edge_device_registry import load_active_device_config, load_device_store
    from labelu.internal.common.system_config_provider import read_group_values, write_group_values

    ciphertext = encrypt_value("existing-secret")
    raw_store = {
        "version": 1,
        "active_device_id": "edge_existing",
        "devices": [
            {
                "id": "edge_existing",
                "name": "existing",
                "host": "192.168.54.104",
                "port": 22,
                "user": "linaro",
                "password_source_type": "inline",
                "password_env_name": "OLD_EDGE_PASSWORD",
                "password_encrypted": ciphertext,
                "password_masked_hint": "********cret",
            }
        ],
    }
    assert write_group_values("edge_device_registry", {"device_store": raw_store})

    restored = load_device_store()
    record = restored["devices"][0]
    assert record["password_encrypted"] == ciphertext
    assert record["password_source_type"] == "mysql:encrypted"
    assert "password_env_name" not in record
    assert load_active_device_config()["password"] == "existing-secret"

    persisted = read_group_values("edge_device_registry")["device_store"]["devices"][0]
    assert persisted["password_encrypted"] == ciphertext
    assert "password_env_name" not in persisted


def test_delete_active_device_is_rejected_when_multiple_devices_exist():
    from labelu.internal.common.edge_device_registry import create_device_record, delete_device_record

    active = create_device_record(
        name="edge-box-01",
        host="10.10.11.26",
        user="linaro",
        password="secret-1111",
        set_active=True,
    )
    create_device_record(
        name="edge-box-02",
        host="10.10.11.27",
        user="linaro",
        password="secret-2222",
        set_active=False,
    )

    with pytest.raises(ValueError):
        delete_device_record(active["id"])


def test_edge_device_registry_supports_private_key_only_and_redacts_path(tmp_path):
    from labelu.internal.common.edge_device_registry import (
        create_device_record,
        load_active_device_config,
        load_device_store,
        resolve_device_private_key_path,
        update_device_record,
    )

    key_path = tmp_path / "id_ed25519"
    key_path.write_text("test-private-key-material", encoding="utf-8")

    record = create_device_record(
        name="key-only-edge",
        host="192.168.54.169",
        user="linaro",
        password=None,
        private_key_path=str(key_path),
        set_active=True,
    )

    persisted = load_device_store()["devices"][0]
    serialized = json.dumps(load_device_store(), ensure_ascii=False)
    assert record["password_is_configured"] is False
    assert persisted["private_key_path_configured"] is True
    assert persisted["private_key_path_source_type"] == "mysql:encrypted"
    assert persisted["private_key_path_masked"] == "********"
    assert persisted["private_key_path_encrypted"] != str(key_path)
    assert "private_key_path" not in persisted
    assert str(key_path) not in serialized
    assert resolve_device_private_key_path(persisted) == str(key_path)

    active = load_active_device_config()
    assert active["password"] == ""
    assert active["private_key_path"] == str(key_path)
    assert active["private_key_path_runtime_available"] is True

    cleared = update_device_record(
        record["id"],
        private_key_path_secret_action="clear",
    )
    assert cleared["private_key_path_configured"] is False
    assert cleared["private_key_path_masked"] == ""
    assert load_active_device_config()["private_key_path"] == ""


def test_edge_device_registry_private_key_path_set_requires_a_value():
    from labelu.internal.application.command.edge_device_registry import (
        PatchEdgeDeviceCommand,
    )

    with pytest.raises(ValueError, match="必须提供 private_key_path"):
        PatchEdgeDeviceCommand(private_key_path_secret_action="set")
