"""AI 标注运行时：附加提示词、视觉链路和多模型档案配置。"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)

DEFAULT_ENABLED = ["volcengine"]
_ALLOWED_PROVIDERS = frozenset(DEFAULT_ENABLED)

PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# 前端编辑但不改密钥时传入该占位，服务端保留原 api_key
API_KEY_KEEP_SENTINEL = "__KEEP__"


def is_qwen_visual_provider(base_url: str, model: str, name: str = "") -> bool:
    """Recognize retired Qwen connections, including named proxy profiles."""
    if any(token in f"{model} {name}".lower() for token in ("qwen", "千问", "通义")):
        return True
    try:
        hostname = (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return False
    return "dashscope" in hostname


def runtime_file_path() -> Path:
    return Path(settings.BASE_DATA_DIR) / "ai_labeling_runtime.json"


def _default_runtime() -> dict[str, Any]:
    return {
        "instruction_suffix": "",
        "enabled_providers": list(DEFAULT_ENABLED),
        "llm_profiles": [],
        "annotation_chain_profile_ids": [],
    }


def _normalize_profiles(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        if not pid or not PROFILE_ID_RE.match(pid):
            continue
        name = str(item.get("name") or "").strip()[:200] or pid
        base_url = str(item.get("base_url") or "").strip()[:500]
        model = str(item.get("model") or "").strip()[:300]
        api_key = str(item.get("api_key") or "").strip()[:2000]
        if is_qwen_visual_provider(base_url, model, name):
            continue
        if not base_url or not model:
            continue
        out.append(
            {
                "id": pid,
                "name": name,
                "base_url": base_url,
                "model": model,
                "api_key": api_key,
            }
        )
    by_id: dict[str, dict[str, Any]] = {}
    for profile in out:
        by_id[profile["id"]] = profile
    return list(by_id.values())


def _normalize_chain_ids(raw: Any, valid_ids: frozenset[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value in valid_ids and value not in seen:
            seen.add(value)
            out.append(value)
    return out[:16]


def load_ai_labeling_runtime_legacy() -> dict[str, Any]:
    base = _default_runtime()
    p = runtime_file_path()
    if not p.is_file():
        return dict(base)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(base)
    if not isinstance(raw, dict):
        return dict(base)
    suffix = raw.get("instruction_suffix") or ""
    enabled = raw.get("enabled_providers")
    if not isinstance(enabled, list) or not enabled:
        enabled = list(DEFAULT_ENABLED)
    enabled = [str(item) for item in enabled if item in _ALLOWED_PROVIDERS]
    if not enabled:
        enabled = list(DEFAULT_ENABLED)
    profiles = _normalize_profiles(raw.get("llm_profiles"))
    valid_ids = frozenset(profile["id"] for profile in profiles)
    chain = _normalize_chain_ids(raw.get("annotation_chain_profile_ids"), valid_ids)

    base["instruction_suffix"] = str(suffix)[:20000]
    base["enabled_providers"] = enabled
    base["llm_profiles"] = profiles
    base["annotation_chain_profile_ids"] = chain
    return base


def _profile_api_key_env_name(profile: dict[str, Any]) -> str:
    existing = str(profile.get("api_key_env_name") or "").strip()
    if existing:
        return existing[:128]
    token = re.sub(r"[^A-Z0-9_]", "_", str(profile.get("id") or "").strip().upper())[:64]
    return f"LABELU_AI_LABELING_PROFILE_{token or 'PROFILE'}_API_KEY"


def _db_profiles_from_runtime(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for profile in _normalize_profiles(profiles):
        out.append(
            {
                "id": profile["id"],
                "name": profile["name"],
                "base_url": profile["base_url"],
                "model": profile["model"],
                "api_key_env_name": _profile_api_key_env_name(profile),
                "api_key_masked": mask_api_key(profile.get("api_key"))[1],
            }
        )
    return out


def _restore_runtime_profiles(
    raw_profiles: Any,
    *,
    legacy_profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_profiles, list):
        return _normalize_profiles(legacy_profiles)
    legacy_by_id = {
        str(item.get("id") or "").strip(): str(item.get("api_key") or "")
        for item in legacy_profiles
        if isinstance(item, dict)
    }
    restored: list[dict[str, Any]] = []
    for item in raw_profiles:
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("id") or "").strip()
        env_name = str(item.get("api_key_env_name") or "").strip()
        api_key = str(os.getenv(env_name) or "") if env_name else ""
        if not api_key:
            api_key = legacy_by_id.get(profile_id, "")
        restored.append(
            {
                "id": profile_id,
                "name": str(item.get("name") or "").strip(),
                "base_url": str(item.get("base_url") or "").strip(),
                "model": str(item.get("model") or "").strip(),
                "api_key": api_key,
                "api_key_env_name": env_name,
            }
        )
    return _normalize_profiles(restored)


def _normalize_runtime_payload(data: dict[str, Any]) -> dict[str, Any]:
    profiles = _normalize_profiles(data.get("llm_profiles"))
    valid_ids = frozenset(profile["id"] for profile in profiles)
    enabled = data.get("enabled_providers")
    if not isinstance(enabled, list) or not enabled:
        enabled = list(DEFAULT_ENABLED)
    enabled = [str(item) for item in enabled if item in _ALLOWED_PROVIDERS]
    if not enabled:
        enabled = list(DEFAULT_ENABLED)
    return {
        "instruction_suffix": str(data.get("instruction_suffix") or "")[:20000],
        "enabled_providers": enabled,
        "llm_profiles": profiles,
        "annotation_chain_profile_ids": _normalize_chain_ids(
            data.get("annotation_chain_profile_ids"),
            valid_ids,
        ),
    }


def _legacy_runtime_to_db_items(data: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_runtime_payload(data)
    return {
        "instruction_suffix": normalized["instruction_suffix"],
        "enabled_providers": normalized["enabled_providers"],
        "llm_profiles": _db_profiles_from_runtime(normalized["llm_profiles"]),
        "annotation_chain_profile_ids": normalized["annotation_chain_profile_ids"],
    }


def _write_legacy_ai_labeling_runtime(data: dict[str, Any]) -> None:
    p = runtime_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ai_labeling_runtime() -> dict[str, Any]:
    legacy = load_ai_labeling_runtime_legacy()
    db_values = read_group_values("ai_labeling_runtime")
    profiles = _restore_runtime_profiles(
        resolve_value(
            runtime_value=get_runtime_override("ai_labeling_runtime", "llm_profiles"),
            db_value=db_values.get("llm_profiles", _db_profiles_from_runtime(legacy["llm_profiles"])),
            default_value=_db_profiles_from_runtime(legacy["llm_profiles"]),
        ),
        legacy_profiles=legacy["llm_profiles"],
    )
    valid_ids = frozenset(profile["id"] for profile in profiles)
    enabled = resolve_value(
        runtime_value=get_runtime_override("ai_labeling_runtime", "enabled_providers"),
        db_value=db_values.get("enabled_providers", legacy["enabled_providers"]),
        default_value=legacy["enabled_providers"],
    )
    return {
        "instruction_suffix": str(
            resolve_value(
                runtime_value=get_runtime_override("ai_labeling_runtime", "instruction_suffix"),
                db_value=db_values.get("instruction_suffix", legacy["instruction_suffix"]),
                default_value=legacy["instruction_suffix"],
            )
            or ""
        )[:20000],
        "enabled_providers": [str(item) for item in enabled if item in _ALLOWED_PROVIDERS]
        or list(DEFAULT_ENABLED),
        "llm_profiles": profiles,
        "annotation_chain_profile_ids": _normalize_chain_ids(
            resolve_value(
                runtime_value=get_runtime_override(
                    "ai_labeling_runtime",
                    "annotation_chain_profile_ids",
                ),
                db_value=db_values.get(
                    "annotation_chain_profile_ids",
                    legacy["annotation_chain_profile_ids"],
                ),
                default_value=legacy["annotation_chain_profile_ids"],
            ),
            valid_ids,
        ),
    }


def write_ai_labeling_runtime(data: dict[str, Any]) -> None:
    normalized = _normalize_runtime_payload(data)
    write_group_values(
        "ai_labeling_runtime",
        _legacy_runtime_to_db_items(normalized),
        value_source="db",
        change_reason="save ai labeling runtime",
    )
    _write_legacy_ai_labeling_runtime(normalized)


def save_ai_labeling_runtime(
    *,
    instruction_suffix: str | None = None,
    enabled_providers: list[str] | None = None,
    llm_profiles: list[dict[str, Any]] | None = None,
    annotation_chain_profile_ids: list[str] | None = None,
) -> dict[str, Any]:
    cur = load_ai_labeling_runtime()
    if instruction_suffix is not None:
        cur["instruction_suffix"] = str(instruction_suffix)[:20000]
    if enabled_providers is not None:
        filtered = [str(item) for item in enabled_providers if item in _ALLOWED_PROVIDERS]
        if filtered:
            cur["enabled_providers"] = filtered
    if llm_profiles is not None:
        cur["llm_profiles"] = _normalize_profiles(llm_profiles)
    if annotation_chain_profile_ids is not None:
        valid = frozenset(profile["id"] for profile in cur["llm_profiles"])
        cur["annotation_chain_profile_ids"] = _normalize_chain_ids(
            annotation_chain_profile_ids,
            valid,
        )
    write_ai_labeling_runtime(cur)
    return load_ai_labeling_runtime()


def new_profile_id() -> str:
    return uuid.uuid4().hex[:16]


def mask_api_key(key: str | None) -> tuple[bool, str]:
    if not key or not str(key).strip():
        return False, ""
    value = str(key).strip()
    if len(value) <= 4:
        return True, "********"
    return True, "*" * 12 + value[-4:]
