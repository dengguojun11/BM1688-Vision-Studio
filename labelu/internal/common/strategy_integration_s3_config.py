from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)

GROUP_KEY = "strategy_integration_s3"
ITEM_KEY = "connections_store"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_store() -> dict[str, Any]:
    return {"version": 1, "active_connection_id": "", "connections": []}


def normalize_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    if "://" not in endpoint:
        endpoint = "http://" + endpoint
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    try:
        _ = parsed.port
    except ValueError:
        return ""
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _legacy_store() -> dict[str, Any]:
    endpoint = normalize_endpoint(
        str(getattr(settings, "STRATEGY_INTEGRATION_S3_ENDPOINT", "") or "")
    )
    if not endpoint:
        return _default_store()
    connection_id = "env_default"
    return {
        "version": 1,
        "active_connection_id": connection_id,
        "connections": [
            {
                "id": connection_id,
                "name": "环境变量配置",
                "endpoint": endpoint,
                "remark": "从 STRATEGY_INTEGRATION_S3_ENDPOINT 兼容读取",
                "created_at": None,
                "updated_at": None,
            }
        ],
    }


def _normalize_connection(record: Any) -> Optional[dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    endpoint = normalize_endpoint(str(record.get("endpoint") or ""))
    if not endpoint:
        return None
    connection_id = str(record.get("id") or "").strip() or f"s3_{uuid.uuid4().hex[:12]}"
    name = str(record.get("name") or "未命名配置").strip()[:64] or "未命名配置"
    return {
        "id": connection_id[:80],
        "name": name,
        "endpoint": endpoint[:500],
        "remark": str(record.get("remark") or "").strip()[:200],
        "created_at": str(record.get("created_at") or "") or None,
        "updated_at": str(record.get("updated_at") or "") or None,
    }


def _restore_store(candidate: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    store = _default_store()
    source = candidate if isinstance(candidate, dict) else fallback
    connections: list[dict[str, Any]] = []
    for item in list(source.get("connections") or []):
        normalized = _normalize_connection(item)
        if normalized:
            connections.append(normalized)
    store["connections"] = connections
    active_id = str(source.get("active_connection_id") or "").strip()
    if active_id and any(item["id"] == active_id for item in connections):
        store["active_connection_id"] = active_id
    elif connections:
        store["active_connection_id"] = connections[0]["id"]
    return store


def load_s3_connection_store() -> dict[str, Any]:
    legacy = _legacy_store()
    db_store = read_group_values(GROUP_KEY).get(ITEM_KEY)
    candidate = resolve_value(
        runtime_value=get_runtime_override(GROUP_KEY, ITEM_KEY),
        db_value=db_store if isinstance(db_store, dict) else None,
        default_value=legacy,
    )
    return _restore_store(candidate, fallback=legacy)


def load_s3_connection_store_legacy() -> dict[str, Any]:
    return _legacy_store()


def _legacy_store_to_db_items(store: dict[str, Any]) -> dict[str, Any]:
    return {ITEM_KEY: _restore_store(store, fallback=_default_store())}


def _write_store(store: dict[str, Any]) -> dict[str, Any]:
    payload = _restore_store(store, fallback=_default_store())
    write_group_values(
        GROUP_KEY,
        _legacy_store_to_db_items(payload),
        value_source="db",
        change_reason="save expert feedback S3 endpoint configuration",
    )
    return payload


def ensure_s3_connection_store_initialized() -> dict[str, Any]:
    values = read_group_values(GROUP_KEY)
    if not isinstance(values.get(ITEM_KEY), dict):
        legacy = _legacy_store()
        if legacy["connections"]:
            try:
                _write_store(legacy)
            except RuntimeError:
                pass
    return load_s3_connection_store()


def list_connection_records() -> list[dict[str, Any]]:
    return list(ensure_s3_connection_store_initialized().get("connections") or [])


def get_active_connection_id() -> str:
    return str(load_s3_connection_store().get("active_connection_id") or "").strip()


def get_active_connection_record() -> Optional[dict[str, Any]]:
    store = load_s3_connection_store()
    active_id = str(store.get("active_connection_id") or "").strip()
    return next(
        (item for item in store.get("connections") or [] if item.get("id") == active_id),
        None,
    )


def get_active_endpoint() -> str:
    record = get_active_connection_record()
    if record:
        return str(record.get("endpoint") or "")
    return normalize_endpoint(
        str(getattr(settings, "STRATEGY_INTEGRATION_S3_ENDPOINT", "") or "")
    )


def create_connection(*, name: str, endpoint: str, remark: str = "", set_active: bool = True) -> dict[str, Any]:
    normalized_endpoint = normalize_endpoint(endpoint)
    if not normalized_endpoint:
        raise ValueError("请输入有效的 S3/MinIO 服务地址，例如 http://192.168.54.190:29100")
    normalized_name = str(name or "").strip()[:64]
    if not normalized_name:
        raise ValueError("请输入配置名称")
    store = load_s3_connection_store()
    now = _utc_now_iso()
    record = {
        "id": f"s3_{uuid.uuid4().hex[:12]}",
        "name": normalized_name,
        "endpoint": normalized_endpoint[:500],
        "remark": str(remark or "").strip()[:200],
        "created_at": now,
        "updated_at": now,
    }
    connections = list(store.get("connections") or [])
    connections.append(record)
    store["connections"] = connections
    if set_active or not store.get("active_connection_id"):
        store["active_connection_id"] = record["id"]
    _write_store(store)
    return record


def update_connection(
    connection_id: str,
    *,
    name: Optional[str] = None,
    endpoint: Optional[str] = None,
    remark: Optional[str] = None,
) -> dict[str, Any]:
    store = load_s3_connection_store()
    target = next(
        (item for item in store.get("connections") or [] if item.get("id") == connection_id),
        None,
    )
    if target is None:
        raise ValueError(f"S3 配置不存在：{connection_id}")
    if name is not None:
        target_name = str(name).strip()[:64]
        if not target_name:
            raise ValueError("请输入配置名称")
        target["name"] = target_name
    if endpoint is not None:
        normalized_endpoint = normalize_endpoint(endpoint)
        if not normalized_endpoint:
            raise ValueError("请输入有效的 S3/MinIO 服务地址")
        target["endpoint"] = normalized_endpoint[:500]
    if remark is not None:
        target["remark"] = str(remark).strip()[:200]
    target["updated_at"] = _utc_now_iso()
    _write_store(store)
    return target


def delete_connection(connection_id: str) -> dict[str, Any]:
    store = load_s3_connection_store()
    connections = list(store.get("connections") or [])
    target = next((item for item in connections if item.get("id") == connection_id), None)
    if target is None:
        raise ValueError(f"S3 配置不存在：{connection_id}")
    if store.get("active_connection_id") == connection_id:
        raise ValueError("当前启用配置不能删除，请先启用其他配置")
    store["connections"] = [item for item in connections if item.get("id") != connection_id]
    return _write_store(store)


def set_active_connection(connection_id: str) -> dict[str, Any]:
    store = load_s3_connection_store()
    if not any(item.get("id") == connection_id for item in store.get("connections") or []):
        raise ValueError(f"S3 配置不存在：{connection_id}")
    store["active_connection_id"] = connection_id
    return _write_store(store)
