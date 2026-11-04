"""Global edge-validation image count settings."""
from __future__ import annotations

from typing import Any

from labelu.internal.common.system_config_provider import (
    read_group_values,
    write_group_values,
)

GROUP_KEY = "edge_validation_settings"
ITEM_KEY = "config"
DEFAULTS = {"version": 1, "min_images": 200, "max_images": 500}


def _normalize(values: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (values or {}).get(ITEM_KEY)
    config = raw if isinstance(raw, dict) else {}
    try:
        minimum = int(config.get("min_images", DEFAULTS["min_images"]))
    except (TypeError, ValueError):
        minimum = DEFAULTS["min_images"]
    try:
        maximum = int(config.get("max_images", DEFAULTS["max_images"]))
    except (TypeError, ValueError):
        maximum = DEFAULTS["max_images"]
    minimum = max(1, min(500, minimum))
    maximum = max(minimum, min(500, maximum))
    return {
        "version": int(config.get("version") or 1),
        "min_images": minimum,
        "max_images": maximum,
    }


def get_config() -> dict[str, Any]:
    return _normalize(read_group_values(GROUP_KEY))


def update_config(
    *, min_images: int, max_images: int, updated_by: int | None = None
) -> dict[str, Any]:
    try:
        minimum = int(min_images)
        maximum = int(max_images)
    except (TypeError, ValueError):
        raise ValueError("边端验证图片数必须是整数") from None
    if minimum < 1 or maximum < minimum or maximum > 500:
        raise ValueError("图片数必须满足 1 <= 最低值 <= 最高值 <= 500")
    current = get_config()
    next_config = {
        "version": int(current["version"]) + 1,
        "min_images": minimum,
        "max_images": maximum,
    }
    if not write_group_values(
        GROUP_KEY,
        {ITEM_KEY: next_config},
        updated_by=updated_by,
        change_reason="update edge validation image limits",
    ):
        raise RuntimeError("边端验证配置保存失败")
    return next_config
