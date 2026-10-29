"""Compatibility facade for the active MySQL-backed edge device."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_edge_ssh_config() -> Dict[str, Any]:
    from labelu.internal.common.edge_device_registry import load_active_device_config

    return load_active_device_config()


def resolve_edge_ssh_password_with_source(
    data: Dict[str, Any] | None = None,
) -> tuple[str, str]:
    payload = data or {}
    password = str(payload.get("password") or "")
    if password:
        return password, str(payload.get("password_source_type") or "mysql:encrypted")

    from labelu.internal.common.edge_device_registry import (
        find_device_record,
        get_active_device_record,
        resolve_device_password_with_source,
    )

    device_id = str(payload.get("device_id") or "").strip()
    record = find_device_record(device_id) if device_id else get_active_device_record()
    return resolve_device_password_with_source(record)


def resolve_edge_ssh_password(data: Dict[str, Any] | None = None) -> str:
    return resolve_edge_ssh_password_with_source(data)[0]


def resolve_edge_ssh_private_key_path_with_source(
    data: Dict[str, Any] | None = None,
) -> tuple[str, str]:
    payload = data or {}
    private_key_path = str(payload.get("private_key_path") or "").strip()
    if private_key_path:
        return private_key_path, str(
            payload.get("private_key_path_source_type") or "mysql:encrypted"
        )

    from labelu.internal.common.edge_device_registry import (
        find_device_record,
        get_active_device_record,
        resolve_device_private_key_path_with_source,
    )

    device_id = str(payload.get("device_id") or "").strip()
    record = find_device_record(device_id) if device_id else get_active_device_record()
    return resolve_device_private_key_path_with_source(record)


def resolve_edge_ssh_private_key_path(data: Dict[str, Any] | None = None) -> str:
    return resolve_edge_ssh_private_key_path_with_source(data)[0]


def save_edge_ssh_config(data: Dict[str, Any]) -> None:
    """Update the active registry device without reading or writing legacy JSON."""
    from labelu.internal.common.edge_device_registry import save_active_device_config

    payload = dict(data or {})
    if not payload.pop("_replace_password", False):
        payload.pop("password", None)
    if not payload.pop("_replace_private_key_path", False):
        payload.pop("private_key_path", None)
    save_active_device_config(payload)


def is_edge_ssh_configured(cfg: Dict[str, Any] | None = None) -> bool:
    current = cfg or load_edge_ssh_config()
    private_key_path = resolve_edge_ssh_private_key_path(current)
    private_key_available = bool(
        private_key_path and Path(private_key_path).expanduser().is_file()
    )
    return bool(
        current.get("enabled")
        and str(current.get("host") or "").strip()
        and str(current.get("user") or "").strip()
        and (resolve_edge_ssh_password(current) or private_key_available)
    )


def should_auto_run_edge_ssh(cfg: Dict[str, Any] | None = None) -> bool:
    current = cfg or load_edge_ssh_config()
    return bool(current.get("auto_after_export") and is_edge_ssh_configured(current))


def prefer_edge_ssh_over_board_agent(cfg: Dict[str, Any] | None = None) -> bool:
    return should_auto_run_edge_ssh(cfg)
