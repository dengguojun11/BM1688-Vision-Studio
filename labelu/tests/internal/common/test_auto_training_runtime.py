from __future__ import annotations

from labelu.internal.common import auto_training_runtime as runtime
from labelu.internal.common import system_config_provider


def test_switch_precedence_runtime_over_env_over_db(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "read_group_values",
        lambda _group: {"train_enabled": False, "scheduler_enabled": False},
    )
    monkeypatch.setattr(
        runtime,
        "get_runtime_override",
        lambda _group, key: (
            True if key == "scheduler_enabled" else system_config_provider._MISSING
        ),
    )
    monkeypatch.setenv("LABELU_AUTO_TRAINING_TRAIN_ENABLED", "true")

    switches = runtime.load_auto_training_switches()

    assert switches["scheduler_enabled"] is True
    assert switches["train_enabled"] is True
    assert switches["bm1688_enabled"] is False


def test_invalid_false_like_environment_value_resolves_false(monkeypatch):
    monkeypatch.setattr(runtime, "read_group_values", lambda _group: {})
    monkeypatch.setattr(
        runtime,
        "get_runtime_override",
        lambda _group, _key: system_config_provider._MISSING,
    )
    monkeypatch.setenv("LABELU_AUTO_TRAINING_EDGE_VALIDATION_ENABLED", "off")

    assert runtime.load_auto_training_switches()["edge_validation_enabled"] is False
