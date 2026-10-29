from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)


def quality_policy_file_path() -> Path:
    return Path(settings.BASE_DATA_DIR) / "yolo_quality_policy.json"


def _default_policy_file() -> dict[str, Any]:
    return {
        "active_policy_name": None,
        "active_policy_names": {"__default__": "default-policy-v1"},
        "policies": [
            {
                "policy_name": "default-policy-v1",
                "scene_name": "\u9ed8\u8ba4\u8d28\u91cf\u6807\u51c6",
                "rect_preset_slug": "__default__",
                "status": "active",
                "source": "system",
                "version": 1,
                "is_active": True,
                "source_summary": {
                    "business_goal": "\u9ed8\u8ba4\u517c\u987e\u7cbe\u5ea6\u3001\u6cdb\u5316\u4e0e\u8fb9\u7aef\u53ef\u7528\u6027",
                    "historical_reference_used": False,
                    "edge_validation_reference_used": False,
                },
                "training_quality": {
                    "map50_95_pass": float(settings.YOLO_VAL_PASS_MAP5095),
                    "map50_warn": float(settings.YOLO_VAL_PASS_MAP50),
                    "min_total_samples": 20,
                    "allow_conditional_pass": True,
                },
                "generalization_quality": {
                    "require_test_metrics": True,
                    "test_map50_95_warn": max(float(settings.YOLO_VAL_PASS_MAP5095) - 0.1, 0.0),
                    "max_gap_map50_95": 0.12,
                    "max_gap_map50": 0.12,
                },
                "edge_usability": {
                    "require_edge_validation": False,
                    "require_eval_when_labeled_images_exist": True,
                    "max_pt_to_bmodel_map50_drop": 0.02,
                },
                "release_readiness": {
                    "block_publish_when_training_failed": True,
                    "block_publish_when_edge_unverified": False,
                    "allow_review_when_generalization_missing": True,
                },
                "key_classes": [],
                "reasons": ["\u7cfb\u7edf\u9ed8\u8ba4\u8d28\u91cf\u6807\u51c6"],
                "risks": [],
                "updated_at": datetime.utcnow().isoformat(),
            }
        ],
    }


def _policy_match_key(item: dict[str, Any]) -> str:
    signature = str(item.get("label_set_signature") or "").strip()
    if signature:
        return "semantic:{family}:{kind}:{signature}".format(
            family=str(item.get("model_family") or "yolov11").strip(),
            kind=str(item.get("annotation_kind") or "bbox").strip(),
            signature=signature,
        )
    return str(item.get("rect_preset_slug") or "").strip()


def _normalize_store(raw: Any) -> dict[str, Any]:
    base = _default_policy_file()
    if not isinstance(raw, dict):
        return base

    policies = raw.get("policies")
    if not isinstance(policies, list) or not policies:
        return base

    active_policy_name = str(raw.get("active_policy_name") or "").strip() or None
    raw_active_names = raw.get("active_policy_names")
    active_policy_names: dict[str, str] = {}
    if isinstance(raw_active_names, dict):
        for key, value in raw_active_names.items():
            slug = str(key or "").strip()
            policy_name = str(value or "").strip()
            if slug and policy_name:
                active_policy_names[slug] = policy_name
    if active_policy_name and "__default__" not in active_policy_names:
        active_policy_names["__default__"] = active_policy_name

    normalized: list[dict[str, Any]] = []
    normalized_active_names: dict[str, str] = {}
    for item in policies:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        policy_name = str(row.get("policy_name") or "").strip()
        slug = _policy_match_key(row)
        is_active = bool(policy_name and slug and active_policy_names.get(slug) == policy_name)
        row["is_active"] = is_active
        if is_active:
            normalized_active_names[slug] = policy_name
        normalized.append(row)

    if not normalized:
        return base

    if not normalized_active_names:
        normalized_active_names = dict(base.get("active_policy_names") or {})
        for row in normalized:
            policy_name = str(row.get("policy_name") or "").strip()
            slug = _policy_match_key(row)
            row["is_active"] = bool(policy_name and slug and normalized_active_names.get(slug) == policy_name)

    known_policy_names = {
        str(item.get("policy_name") or "").strip()
        for item in normalized
        if isinstance(item, dict)
    }
    if not active_policy_name or active_policy_name not in known_policy_names:
        active_policy_name = (
            normalized_active_names.get("__default__")
            or next(iter(normalized_active_names.values()), None)
        )

    return {
        "active_policy_name": active_policy_name,
        "active_policy_names": normalized_active_names,
        "policies": normalized,
    }


def load_quality_policy_store_legacy() -> dict[str, Any]:
    path = quality_policy_file_path()
    if not path.is_file():
        return _default_policy_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default_policy_file()
    return _normalize_store(raw)


def _legacy_quality_policy_store_to_db_items(data: dict[str, Any]) -> dict[str, Any]:
    return {"store": _normalize_store(data)}


def _write_legacy_quality_policy_store(data: dict[str, Any]) -> None:
    path = quality_policy_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_quality_policy_store() -> dict[str, Any]:
    legacy = load_quality_policy_store_legacy()
    db_store = read_group_values("yolo_quality_policy_store").get("store")
    store = resolve_value(
        runtime_value=get_runtime_override("yolo_quality_policy_store", "store"),
        db_value=db_store if isinstance(db_store, dict) else legacy,
        default_value=legacy,
    )
    return _normalize_store(store)


def save_quality_policy_store(data: dict[str, Any]) -> dict[str, Any]:
    payload = _normalize_store(data)
    write_group_values(
        "yolo_quality_policy_store",
        {"store": payload},
        value_source="db",
        change_reason="save yolo quality policy store",
    )
    _write_legacy_quality_policy_store(payload)
    return load_quality_policy_store()
