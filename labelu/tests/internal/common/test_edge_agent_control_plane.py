from __future__ import annotations

from labelu.internal.common import edge_agent_control_plane as control


def test_environment_values_override_db_overlay(monkeypatch):
    monkeypatch.setattr(
        control,
        "_explicit_env_values",
        lambda: {
            "BOARD_AGENT_ENABLED": "true",
            "BOARD_AGENT_PRIMARY_ENABLED": "true",
            "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED": "true",
            "BOARD_AGENT_TOKEN": "env-token",
            "BOARD_AGENT_PUBLIC_BASE_URL": "http://edge-host:8003",
        },
    )
    monkeypatch.setattr(
        control,
        "_db_config",
        lambda: {
            "enabled": False,
            "primary_enabled": False,
            "auto_bootstrap_enabled": False,
            "public_base_url": "http://db-host:8003",
            "token_encrypted": "not-used",
        },
    )

    resolved = control.resolve_control_plane()

    assert resolved.control_plane_enabled is True
    assert resolved.token == "env-token"
    assert resolved.public_base_url == "http://edge-host:8003"
    assert resolved.source == "env"
    assert resolved.token_source == "env"


def test_page_auto_configuration_generates_only_encrypted_secret(monkeypatch):
    stored: dict[str, dict] = {}
    db_values: dict = {}

    monkeypatch.setattr(control, "_explicit_env_values", lambda: {})
    monkeypatch.setattr(control, "_db_config", lambda: dict(db_values))
    monkeypatch.setattr(control, "derive_public_base_url", lambda **_: "http://192.168.1.10:8003")
    monkeypatch.setattr(control, "encrypt_value", lambda value: f"cipher:{value}")
    monkeypatch.setattr(control, "decrypt_value", lambda value: str(value).removeprefix("cipher:"))
    monkeypatch.setattr(
        control,
        "write_group_values",
        lambda _group, values, **_: (db_values.update(values[control.ITEM_KEY]), stored.update(values)),
    )
    monkeypatch.setattr(control.settings, "APP_ENV", "development")
    monkeypatch.setattr(control.settings, "BOARD_AGENT_TASK_TOKEN_SECRET", "")
    generated = iter(("agent-token-generated", "task-secret-generated"))
    monkeypatch.setattr(control.secrets, "token_urlsafe", lambda _size: next(generated))

    result = control.ensure_auto_configured(device_host="192.168.1.20")

    assert result.ok is True
    assert result.changed is True
    assert result.config.control_plane_enabled is True
    assert result.config.public_base_url == "http://192.168.1.10:8003"
    assert result.config.token
    assert str(db_values["token_encrypted"]).startswith("cipher:")
    assert str(db_values["task_token_secret_encrypted"]).startswith("cipher:")
    assert db_values["token_encrypted"] != db_values["task_token_secret_encrypted"]
    assert "token" not in db_values
    assert "BOARD_AGENT_TOKEN" not in db_values


def test_page_auto_configuration_reuses_valid_existing_task_secret(monkeypatch):
    db_values = {
        "enabled": True,
        "primary_enabled": True,
        "auto_bootstrap_enabled": True,
        "public_base_url": "http://192.168.1.10:8003",
        "token_encrypted": "cipher:agent-token",
        "task_token_secret_encrypted": "cipher:existing-task-secret",
    }

    monkeypatch.setattr(control, "_explicit_env_values", lambda: {})
    monkeypatch.setattr(control, "_db_config", lambda: dict(db_values))
    monkeypatch.setattr(control, "decrypt_value", lambda value: str(value).removeprefix("cipher:"))
    monkeypatch.setattr(control, "encrypt_value", lambda value: f"cipher:{value}")
    persist = []
    monkeypatch.setattr(
        control,
        "write_group_values",
        lambda _group, values, **_: persist.append(values),
    )
    monkeypatch.setattr(control.settings, "APP_ENV", "development")

    result = control.ensure_auto_configured(device_host="192.168.1.20")

    assert result.ok is True
    assert result.changed is False
    assert result.config.task_token_secret == "existing-task-secret"
    assert persist == []


def test_production_requires_static_environment_configuration(monkeypatch):
    monkeypatch.setattr(control, "_explicit_env_values", lambda: {})
    monkeypatch.setattr(control, "_db_config", lambda: {})
    monkeypatch.setattr(control.settings, "APP_ENV", "production")

    result = control.ensure_auto_configured(device_host="192.168.1.20")

    assert result.ok is False
    assert result.changed is False
    assert result.code == "production_env_required"
    assert result.config.control_plane_enabled is False


def test_production_projection_keeps_static_configuration_diagnostic(monkeypatch):
    monkeypatch.setattr(control, "_explicit_env_values", lambda: {})
    monkeypatch.setattr(control, "_db_config", lambda: {})
    monkeypatch.setattr(control.settings, "APP_ENV", "production")

    resolved = control.resolve_control_plane()

    assert resolved.control_plane_enabled is False
    assert resolved.message == "生产环境必须先由环境变量/.env提供边端 Agent 控制面配置"


def test_invalid_task_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(
        control,
        "_explicit_env_values",
        lambda: {
            "BOARD_AGENT_ENABLED": "true",
            "BOARD_AGENT_PRIMARY_ENABLED": "true",
            "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED": "true",
            "BOARD_AGENT_TOKEN": "agent-token",
            "BOARD_AGENT_PUBLIC_BASE_URL": "http://edge-host:8003",
        },
    )
    monkeypatch.setattr(
        control,
        "_db_config",
        lambda: {"task_token_secret_encrypted": "bad-ciphertext"},
    )
    monkeypatch.setattr(control, "_decrypt_secret", lambda _value: ("", "invalid"))

    resolved = control.resolve_control_plane()

    assert resolved.task_token_secret == ""
    assert resolved.control_plane_enabled is False
    assert "任务令牌密钥无法解密" in resolved.message

