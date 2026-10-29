"""Training-specific sample metadata shared by annotation and YOLO export flows."""

from __future__ import annotations

from typing import Any

TRAINING_SAMPLE_TYPE_KEY = "training_sample_type"
TRAINING_SAMPLE_TYPE_NEGATIVE = "negative"


def training_sample_type(sample_data: Any) -> str:
    if not isinstance(sample_data, dict):
        return ""
    return str(sample_data.get(TRAINING_SAMPLE_TYPE_KEY) or "").strip().lower()


def is_explicit_negative_sample(sample_data: Any) -> bool:
    return training_sample_type(sample_data) == TRAINING_SAMPLE_TYPE_NEGATIVE


def rect_annotation_items(result: Any) -> list[Any]:
    if not isinstance(result, dict):
        return []
    rect_block = result.get("rectTool") or result.get("rect") or {}
    if isinstance(rect_block, dict):
        items = rect_block.get("result") or []
    else:
        items = rect_block or []
    return items if isinstance(items, list) else []
