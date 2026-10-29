from __future__ import annotations

import os
from typing import Any

from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
)

AUTO_TRAINING_SWITCH_DEFAULTS = {
    "schema_ready": False,
    "observe_only": False,
    "outbox_enabled": False,
    "pool_enabled": False,
    "scheduler_enabled": False,
    # Database lease election is enabled by default once the schema/runtime
    # switch is enabled; set false only for controlled single-process tests.
    "scheduler_leader_enabled": True,
    "train_enabled": False,
    # Publish non-YOLO adapter jobs through the versioned worker command
    # protocol.  Legacy YOLO/plate jobs remain on the existing queue.
    "worker_command_enabled": False,
    "bm1688_enabled": False,
    "edge_validation_enabled": False,
}


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_auto_training_switches() -> dict[str, bool]:
    db_values = read_group_values("auto_training")
    resolved: dict[str, bool] = {}
    for key, default in AUTO_TRAINING_SWITCH_DEFAULTS.items():
        env_value = _env_bool(f"LABELU_AUTO_TRAINING_{key.upper()}")
        candidates: dict[str, Any] = {
            "runtime_value": get_runtime_override("auto_training", key),
            "db_value": db_values.get(key, default),
            "default_value": default,
        }
        if env_value is not None:
            candidates["env_value"] = env_value
        value: Any = resolve_value(
            **candidates,
        )
        resolved[key] = bool(value)
    return resolved
