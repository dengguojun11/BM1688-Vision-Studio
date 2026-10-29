"""Compatibility SSH reads are backed by the active MySQL device."""

import json

from labelu.internal.common import edge_ssh_config as cfg_io


def test_should_auto_run_when_mysql_password_is_available():
    from labelu.internal.common.edge_device_registry import load_device_store

    cfg_io.save_edge_ssh_config(
        {
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret",
            "_replace_password": True,
        }
    )
    loaded = cfg_io.load_edge_ssh_config()
    assert loaded["enabled"] is True
    assert loaded["auto_after_export"] is True
    assert loaded["password"] == "secret"
    assert cfg_io.should_auto_run_edge_ssh(loaded) is True
    assert "secret" not in json.dumps(load_device_store(), ensure_ascii=False)


def test_load_edge_ssh_config_uses_active_edge_device_registry():
    from labelu.internal.common.edge_device_registry import create_device_record, set_active_device_id

    create_device_record(
        name="edge-box-01",
        host="10.10.11.26",
        user="linaro",
        password="secret-box-1",
        set_active=True,
    )
    rec2 = create_device_record(
        name="edge-box-02",
        host="10.10.11.27",
        user="linaro",
        password="secret-box-2",
        set_active=False,
    )
    set_active_device_id(rec2["id"])

    loaded = cfg_io.load_edge_ssh_config()
    assert loaded["host"] == "10.10.11.27"
    assert loaded["password"] == "secret-box-2"


def test_should_not_auto_run_when_private_key_path_is_missing(tmp_path):
    cfg = {
        "enabled": True,
        "auto_after_export": True,
        "host": "10.10.11.26",
        "user": "linaro",
        "private_key_path": str(tmp_path / "missing-id-ed25519"),
    }

    assert cfg_io.is_edge_ssh_configured(cfg) is False
    assert cfg_io.should_auto_run_edge_ssh(cfg) is False
