"""远程 YOLO 训练节点配置，治理表优先，保留旧 JSON 兼容回退。"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)

CONFIG_FILENAME = "remote_train_nodes.json"
_NODE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def config_file_path() -> Path:
    return Path(settings.BASE_DATA_DIR) / CONFIG_FILENAME


def _normalize_node(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    node_id = str(raw.get("id") or "").strip()
    if not node_id or not _NODE_ID_RE.match(node_id):
        return None
    host = str(raw.get("host") or "").strip()
    if not host:
        return None
    remote_base = str(raw.get("remote_base") or "").strip()
    python_path = str(raw.get("python_path") or "").strip()
    if not remote_base or not python_path:
        return None
    return {
        "id": node_id,
        "name": str(raw.get("name") or node_id).strip()[:128],
        "enabled": bool(raw.get("enabled", True)),
        "host": host,
        "port": int(raw.get("port") or 22),
        "user": str(raw.get("user") or "root").strip()[:128],
        "password": str(raw.get("password") or ""),
        "private_key_path": str(raw.get("private_key_path") or "").strip(),
        "remote_base": remote_base,
        "python_path": python_path,
        "agent_path": str(raw.get("agent_path") or "").strip(),
        "weights_cache_dir": str(raw.get("weights_cache_dir") or "").strip(),
        "cuda_devices": str(raw.get("cuda_devices") or "0").strip()[:64],
        "payload_download_host": str(raw.get("payload_download_host") or "").strip(),
        "max_concurrent_jobs": max(1, min(8, int(raw.get("max_concurrent_jobs") or 1))),
        "env_ready": bool(raw.get("env_ready")),
        "last_test_at": str(raw.get("last_test_at") or "").strip() or None,
        "last_test_message": str(raw.get("last_test_message") or "").strip() or None,
    }


def load_remote_train_nodes_config_legacy() -> Dict[str, Any]:
    data: Dict[str, Any] = {"nodes": []}
    path = config_file_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (json.JSONDecodeError, OSError):
            data = {"nodes": []}
    nodes_in = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    nodes: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in nodes_in:
        norm = _normalize_node(item if isinstance(item, dict) else {})
        if not norm or norm["id"] in seen:
            continue
        seen.add(norm["id"])
        nodes.append(norm)
    return {"nodes": nodes}


def _node_password_env_name(node: dict[str, Any]) -> str:
    existing = str(node.get("password_env_name") or "").strip()
    if existing:
        return existing[:128]
    token = re.sub(r"[^A-Z0-9_]", "_", str(node.get("id") or "").strip().upper())[:64]
    return f"LABELU_REMOTE_TRAIN_NODE_{token or 'NODE'}_PASSWORD"


def _node_key_env_name(node: dict[str, Any]) -> str:
    existing = str(node.get("private_key_path_env_name") or "").strip()
    if existing:
        return existing[:128]
    token = re.sub(r"[^A-Z0-9_]", "_", str(node.get("id") or "").strip().upper())[:64]
    return f"LABELU_REMOTE_TRAIN_NODE_{token or 'NODE'}_PRIVATE_KEY_PATH"


def _db_nodes_from_runtime(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        norm = _normalize_node(node)
        if not norm:
            continue
        out.append(
            {
                **{k: v for k, v in norm.items() if k not in {"password", "private_key_path"}},
                "password_env_name": _node_password_env_name(node),
                "password_masked": "********" if norm.get("password") else "",
                "private_key_path_env_name": _node_key_env_name(node),
                "private_key_path_masked": "********" if norm.get("private_key_path") else "",
            }
        )
    return out


def _restore_runtime_nodes(raw_nodes: Any, *, legacy_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw_nodes, list):
        return load_remote_train_nodes_config_legacy()["nodes"]
    legacy_by_id = {
        str(item.get("id") or "").strip(): item
        for item in legacy_nodes
        if isinstance(item, dict)
    }
    out: list[dict[str, Any]] = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or "").strip()
        legacy = legacy_by_id.get(node_id) or {}
        restored = {
            **item,
            "password": str(
                os.getenv(str(item.get("password_env_name") or "").strip()) or legacy.get("password") or ""
            ),
            "private_key_path": str(
                os.getenv(str(item.get("private_key_path_env_name") or "").strip())
                or legacy.get("private_key_path")
                or ""
            ).strip(),
        }
        norm = _normalize_node(restored)
        if norm:
            out.append(norm)
    return out


def _legacy_remote_train_nodes_to_db_items(data: Dict[str, Any]) -> dict[str, Any]:
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    return {"nodes": _db_nodes_from_runtime(nodes)}


def _write_legacy_remote_train_nodes_config(data: Dict[str, Any]) -> None:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_remote_train_nodes_config() -> Dict[str, Any]:
    legacy = load_remote_train_nodes_config_legacy()
    db_nodes = read_group_values("remote_train_nodes").get("nodes")
    nodes = _restore_runtime_nodes(
        resolve_value(
            runtime_value=get_runtime_override("remote_train_nodes", "nodes"),
            db_value=db_nodes if isinstance(db_nodes, list) else _db_nodes_from_runtime(legacy["nodes"]),
            default_value=_db_nodes_from_runtime(legacy["nodes"]),
        ),
        legacy_nodes=legacy["nodes"],
    )
    return {"nodes": nodes}


def save_remote_train_nodes_config(data: Dict[str, Any]) -> None:
    path_payload = {"nodes": []}
    nodes_in = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    merged: List[Dict[str, Any]] = []
    existing = {
        n["id"]: n
        for n in load_remote_train_nodes_config().get("nodes", [])
        if isinstance(n, dict) and n.get("id")
    }
    for item in nodes_in:
        if not isinstance(item, dict):
            continue
        norm = _normalize_node(item)
        if not norm:
            continue
        prev = existing.get(norm["id"]) or {}
        if not norm.get("password") and prev.get("password"):
            norm["password"] = prev["password"]
        if not norm.get("private_key_path") and prev.get("private_key_path"):
            norm["private_key_path"] = prev["private_key_path"]
        if "env_ready" not in item:
            norm["env_ready"] = bool(prev.get("env_ready"))
        if "last_test_at" not in item:
            norm["last_test_at"] = prev.get("last_test_at")
        if "last_test_message" not in item:
            norm["last_test_message"] = prev.get("last_test_message")
        merged.append(norm)
    path_payload["nodes"] = merged
    write_group_values(
        "remote_train_nodes",
        _legacy_remote_train_nodes_to_db_items(path_payload),
        value_source="db",
        change_reason="save remote train nodes config",
    )
    _write_legacy_remote_train_nodes_config(path_payload)


def get_node_by_id(node_id: str) -> Optional[Dict[str, Any]]:
    nid = (node_id or "").strip()
    if not nid:
        return None
    for node in load_remote_train_nodes_config().get("nodes", []):
        if isinstance(node, dict) and str(node.get("id")) == nid:
            return deepcopy(node)
    return None


def list_enabled_nodes(*, env_ready_only: bool = False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in load_remote_train_nodes_config().get("nodes", []):
        if not isinstance(node, dict) or not node.get("enabled"):
            continue
        if env_ready_only and not node.get("env_ready"):
            continue
        out.append(deepcopy(node))
    return out


def update_node_test_result(
    node_id: str,
    *,
    ok: bool,
    message: str,
    env_ready: bool,
    tested_at: str,
) -> None:
    cfg = load_remote_train_nodes_config()
    changed = False
    for node in cfg.get("nodes", []):
        if not isinstance(node, dict) or str(node.get("id")) != node_id:
            continue
        node["env_ready"] = bool(env_ready)
        node["last_test_at"] = tested_at
        node["last_test_message"] = (message or "")[:500]
        changed = True
        break
    if changed:
        save_remote_train_nodes_config(cfg)
