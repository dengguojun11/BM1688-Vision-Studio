from __future__ import annotations

import copy
import json
from typing import Any, Iterable


def parse_sample_result(sample_data: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the outer sample payload and its decoded annotation result."""
    if isinstance(sample_data, str):
        try:
            sample_data = json.loads(sample_data)
        except (TypeError, ValueError, json.JSONDecodeError):
            sample_data = {}
    outer = sample_data if isinstance(sample_data, dict) else {}
    result = outer.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError, json.JSONDecodeError):
            result = {}
    return outer, result if isinstance(result, dict) else {}


def rect_label_values(sample_data: Any) -> set[str]:
    """Return the distinct, non-empty labels used by rect annotations."""
    _, result = parse_sample_result(sample_data)
    rect_tool = result.get("rectTool")
    rects = rect_tool.get("result") if isinstance(rect_tool, dict) else []
    if not isinstance(rects, list):
        return set()
    return {
        str(rect.get("label") or "").strip()
        for rect in rects
        if isinstance(rect, dict) and str(rect.get("label") or "").strip()
    }


def label_rectangles(sample_data: Any, label_values: Iterable[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, result = parse_sample_result(sample_data)
    values = {str(value).strip() for value in label_values if str(value).strip()}
    rect_tool = result.get("rectTool")
    rects = rect_tool.get("result") if isinstance(rect_tool, dict) else []
    if not isinstance(rects, list):
        return result, []
    matches = [
        copy.deepcopy(rect)
        for rect in rects
        if isinstance(rect, dict) and str(rect.get("label") or "").strip() in values
    ]
    return result, matches


def sample_data_with_only_label(sample_data: Any, label_values: Iterable[str]) -> dict[str, Any] | None:
    outer, result = parse_sample_result(sample_data)
    _, matches = label_rectangles(sample_data, label_values)
    if not matches:
        return None
    filtered_result = copy.deepcopy(result)
    rect_tool = filtered_result.get("rectTool")
    if isinstance(rect_tool, dict):
        rect_tool["result"] = matches
        filtered_result["rectTool"] = rect_tool
    filtered_outer = copy.deepcopy(outer)
    filtered_outer["result"] = json.dumps(filtered_result, ensure_ascii=False)
    return filtered_outer
