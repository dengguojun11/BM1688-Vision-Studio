from __future__ import annotations

import json
from statistics import median
from typing import Any


def _json_loads_maybe(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _block_result_items(block: Any) -> list[Any]:
    if isinstance(block, dict):
        items = block.get("result")
        if isinstance(items, list):
            return items
    return []


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def convert_uploaded_pre_annotation_to_sample_result(raw_payload: Any) -> dict[str, Any]:
    """Convert uploaded pre-annotation payloads into sample.result format.

    Supports both historical JSON upload payloads:
    - {"result": {"width", "height", "rotate", "annotations": [...]}}
    - {"meta_data": {...}, "annotations": {...}}
    - already-normalized sample.result payloads with rectTool/pointTool...
    """

    payload = _json_loads_maybe(raw_payload) or {}
    if not isinstance(payload, dict):
        raise ValueError("pre-annotation payload must be an object")

    if any(key.endswith("Tool") for key in payload.keys()):
        return payload

    if isinstance(payload.get("result"), dict):
        result = payload["result"]
        annotations = result.get("annotations")
        if isinstance(annotations, list):
            out = {
                "width": result.get("width"),
                "height": result.get("height"),
                "rotate": result.get("rotate", 0),
            }
            for block in annotations:
                if not isinstance(block, dict):
                    continue
                tool_name = block.get("toolName")
                if not tool_name:
                    continue
                out[tool_name] = {
                    "toolName": tool_name,
                    "result": block.get("result", []),
                }
            return out

    annotations = payload.get("annotations")
    meta_data = payload.get("meta_data")
    if isinstance(annotations, dict) and isinstance(meta_data, dict):
        out = {
            "width": meta_data.get("width"),
            "height": meta_data.get("height"),
            "rotate": meta_data.get("rotate", 0),
        }
        for tool_name, block in annotations.items():
            if not isinstance(block, dict):
                continue
            out[tool_name] = {
                "toolName": block.get("toolName", tool_name),
                "result": block.get("result", []),
            }
        return out

    raise ValueError("unsupported uploaded pre-annotation payload shape")


def count_annotation_items(result_payload: Any) -> int:
    payload = _json_loads_maybe(result_payload) or {}
    if not isinstance(payload, dict):
        return 0
    return sum(
        len(_block_result_items(block))
        for key, block in payload.items()
        if isinstance(key, str) and key.endswith("Tool")
    )


def count_out_of_bounds_rects(result_payload: Any) -> int:
    payload = _json_loads_maybe(result_payload) or {}
    if not isinstance(payload, dict):
        return 0

    width = payload.get("width")
    height = payload.get("height")
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return 0

    bad = 0
    for item in _block_result_items(payload.get("rectTool")):
        if not isinstance(item, dict):
            continue
        x = item.get("x")
        y = item.get("y")
        w = item.get("width")
        h = item.get("height")
        if not all(isinstance(v, (int, float)) for v in (x, y, w, h)):
            continue
        if x < 0 or y < 0 or x + w > width or y + h > height:
            bad += 1
    return bad


def get_rect_items_by_id(result_payload: Any) -> dict[str, dict[str, Any]]:
    payload = _json_loads_maybe(result_payload) or {}
    if not isinstance(payload, dict):
        return {}

    rects: dict[str, dict[str, Any]] = {}
    for item in _block_result_items(payload.get("rectTool")):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            rects[item_id] = item
    return rects


def _safe_ratio(current: Any, expected: Any) -> float | None:
    current_num = _numeric(current)
    expected_num = _numeric(expected)
    if current_num is None or expected_num is None or abs(expected_num) < 1e-6:
        return None
    return current_num / expected_num


def _median_or_default(values: list[float], default: float = 1.0) -> float:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return default
    return float(median(cleaned))


def _median_relative_deviation(values: list[float], center: float) -> float:
    cleaned = [value for value in values if value > 0]
    if not cleaned or center <= 0:
        return 0.0
    deviations = [abs(value - center) / center for value in cleaned]
    return float(median(deviations))


def _rect_matches(
    current: dict[str, Any],
    expected: dict[str, Any],
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> bool:
    x = _numeric(current.get("x"))
    y = _numeric(current.get("y"))
    width = _numeric(current.get("width"))
    height = _numeric(current.get("height"))
    expected_x = _numeric(expected.get("x"))
    expected_y = _numeric(expected.get("y"))
    expected_width = _numeric(expected.get("width"))
    expected_height = _numeric(expected.get("height"))
    if None in (x, y, width, height, expected_x, expected_y, expected_width, expected_height):
        return False

    unscaled = {
        "x": x / scale_x if scale_x else x,
        "y": y / scale_y if scale_y else y,
        "width": width / scale_x if scale_x else width,
        "height": height / scale_y if scale_y else height,
    }
    tolerances = {
        "x": max(3.0, abs(expected_width) * 0.03),
        "y": max(3.0, abs(expected_height) * 0.03),
        "width": max(3.0, abs(expected_width) * 0.03),
        "height": max(3.0, abs(expected_height) * 0.03),
    }
    return (
        abs(unscaled["x"] - expected_x) <= tolerances["x"]
        and abs(unscaled["y"] - expected_y) <= tolerances["y"]
        and abs(unscaled["width"] - expected_width) <= tolerances["width"]
        and abs(unscaled["height"] - expected_height) <= tolerances["height"]
    )


def detect_systematic_rect_scaling_issue(
    current_result_payload: Any,
    expected_result_payload: Any,
) -> dict[str, Any]:
    current_rects = get_rect_items_by_id(current_result_payload)
    expected_rects = get_rect_items_by_id(expected_result_payload)
    shared_ids = sorted(set(current_rects).intersection(expected_rects))

    width_ratios = [
        ratio
        for ratio in (_safe_ratio(current_rects[item_id].get("width"), expected_rects[item_id].get("width"))
                      for item_id in shared_ids)
        if ratio is not None
    ]
    height_ratios = [
        ratio
        for ratio in (_safe_ratio(current_rects[item_id].get("height"), expected_rects[item_id].get("height"))
                      for item_id in shared_ids)
        if ratio is not None
    ]
    scale_x = _median_or_default(width_ratios)
    scale_y = _median_or_default(height_ratios)
    direct_match_count = sum(
        1 for item_id in shared_ids if _rect_matches(current_rects[item_id], expected_rects[item_id])
    )
    unscaled_match_count = sum(
        1
        for item_id in shared_ids
        if _rect_matches(
            current_rects[item_id],
            expected_rects[item_id],
            scale_x=scale_x,
            scale_y=scale_y,
        )
    )
    shared_count = len(shared_ids)
    direct_match_ratio = direct_match_count / shared_count if shared_count else 0.0
    unscaled_match_ratio = unscaled_match_count / shared_count if shared_count else 0.0
    width_ratio_deviation = _median_relative_deviation(width_ratios, scale_x)
    height_ratio_deviation = _median_relative_deviation(height_ratios, scale_y)
    ids_equal = set(current_rects) == set(expected_rects) and len(current_rects) == len(expected_rects)
    scale_shifted = abs(scale_x - 1.0) >= 0.05 or abs(scale_y - 1.0) >= 0.05
    stable_scale = width_ratio_deviation <= 0.05 and height_ratio_deviation <= 0.05
    suspicious = (
        ids_equal
        and shared_count >= 3
        and scale_shifted
        and stable_scale
        and direct_match_ratio <= 0.4
        and unscaled_match_ratio >= 0.8
    )
    reasons: list[str] = []
    if suspicious:
        reasons.append("systematic_rect_scaling")
    if not ids_equal and shared_count:
        reasons.append("rect_id_set_changed")

    return {
        "suspicious": suspicious,
        "reasons": reasons,
        "current_rect_count": len(current_rects),
        "expected_rect_count": len(expected_rects),
        "shared_rect_count": shared_count,
        "ids_equal": ids_equal,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "width_ratio_deviation": width_ratio_deviation,
        "height_ratio_deviation": height_ratio_deviation,
        "direct_match_ratio": direct_match_ratio,
        "unscaled_match_ratio": unscaled_match_ratio,
    }
