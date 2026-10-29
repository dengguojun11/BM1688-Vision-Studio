from __future__ import annotations

import copy
import json
from typing import Any

from labelu.internal.application.service.yolo_dataset import build_yolo_rect_class_names


_LEGACY_RECT_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "streetVendor_vegetablet": ("streetVendor_vegetable", "vegetable_stall"),
    "streetVendor_catering": ("catering_vendor",),
    "streetVendor_clother": ("streetVendor_clothing", "clothing_vendor"),
    "streetVendor_fruit": ("fruit_vendor",),
    "streetVendor_other": ("other_vendor",),
    "object_plasticbasket": ("box",),
    "object_basket": ("basket",),
    "object_bucket": ("bucket", "object_bauket"),
    "object_cabinet": ("cabinet",),
    "object_table": ("table",),
    "object_chair": ("chair",),
}


def _allowed_rect_labels(task_config: dict[str, Any] | None) -> list[str]:
    return build_yolo_rect_class_names(task_config or {})


def _alias_map_for_allowed_labels(allowed_labels: list[str]) -> dict[str, str]:
    allowed = {str(item).strip() for item in allowed_labels if str(item).strip()}
    out: dict[str, str] = {}
    for canonical, aliases in _LEGACY_RECT_LABEL_ALIASES.items():
        if canonical not in allowed:
            continue
        for alias in aliases:
            if alias and alias not in allowed:
                out[alias] = canonical
    return out


def _normalize_label(label: Any, alias_map: dict[str, str]) -> str:
    raw = str(label or "").strip()
    if not raw:
        return ""
    return alias_map.get(raw, raw)


def _normalize_rect_items(
    items: list[Any],
    *,
    allowed_labels: list[str],
    alias_map: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    replaced: dict[str, str] = {}
    unknown: set[str] = set()
    allowed = set(allowed_labels)
    for item in items:
        if not isinstance(item, dict):
            continue
        before = str(item.get("label") or "").strip()
        if not before:
            continue
        after = _normalize_label(before, alias_map)
        if after != before:
            item["label"] = after
            replaced[before] = after
        if after not in allowed:
            unknown.add(after)
    return replaced, sorted(unknown)


def normalize_sample_api_data_rect_labels(
    sample_data: dict[str, Any] | None,
    *,
    task_config: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, str], list[str], list[str]]:
    if not isinstance(sample_data, dict):
        return sample_data, {}, [], []

    allowed_labels = _allowed_rect_labels(task_config)
    if not allowed_labels:
        return copy.deepcopy(sample_data), {}, [], []

    result_raw = sample_data.get("result")
    if result_raw is None:
        return copy.deepcopy(sample_data), {}, [], allowed_labels

    result_payload = result_raw
    if isinstance(result_payload, str):
        try:
            result_payload = json.loads(result_payload)
        except (TypeError, json.JSONDecodeError):
            return copy.deepcopy(sample_data), {}, [], allowed_labels

    if not isinstance(result_payload, dict):
        return copy.deepcopy(sample_data), {}, [], allowed_labels

    alias_map = _alias_map_for_allowed_labels(allowed_labels)
    normalized_result = copy.deepcopy(result_payload)
    rect_items = ((normalized_result.get("rectTool") or {}).get("result") or [])
    replaced, unknown = _normalize_rect_items(
        rect_items,
        allowed_labels=allowed_labels,
        alias_map=alias_map,
    )

    normalized_data = copy.deepcopy(sample_data)
    if isinstance(result_raw, str):
        normalized_data["result"] = json.dumps(normalized_result, ensure_ascii=False)
    else:
        normalized_data["result"] = normalized_result
    return normalized_data, replaced, unknown, allowed_labels


def normalize_uploaded_pre_annotation_rect_labels(
    payload: dict[str, Any] | None,
    *,
    task_config: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, str], list[str], list[str]]:
    if not isinstance(payload, dict):
        return payload, {}, [], []

    allowed_labels = _allowed_rect_labels(task_config)
    if not allowed_labels:
        return copy.deepcopy(payload), {}, [], []

    alias_map = _alias_map_for_allowed_labels(allowed_labels)
    normalized = copy.deepcopy(payload)
    replaced: dict[str, str] = {}
    unknown: set[str] = set()

    def apply_items(items: list[Any]) -> None:
        nonlocal replaced, unknown
        cur_replaced, cur_unknown = _normalize_rect_items(
            items,
            allowed_labels=allowed_labels,
            alias_map=alias_map,
        )
        replaced.update(cur_replaced)
        unknown.update(cur_unknown)

    result_block = normalized.get("result")
    if isinstance(result_block, dict):
        annotations = result_block.get("annotations")
        if isinstance(annotations, list):
            for block in annotations:
                if isinstance(block, dict) and block.get("toolName") == "rectTool":
                    apply_items(block.get("result") or [])

    annotations = normalized.get("annotations")
    if isinstance(annotations, list):
        for block in annotations:
            if isinstance(block, dict) and block.get("toolName") == "rectTool":
                apply_items(block.get("result") or [])
    elif isinstance(annotations, dict):
        rect_block = annotations.get("rectTool")
        if isinstance(rect_block, dict):
            apply_items(rect_block.get("result") or [])

    rect_block = normalized.get("rectTool")
    if isinstance(rect_block, dict):
        apply_items(rect_block.get("result") or [])

    return normalized, replaced, sorted(unknown), allowed_labels
