"""Training admission configuration.

The effective minimum sample count is stored in the system configuration
governance store.  The environment setting remains the default/fallback so
existing installations keep working without a migration.
"""
from __future__ import annotations

from typing import Any

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    read_group_values,
    write_group_values,
)

GROUP_KEY = "training_admission"
ITEM_KEY = "config"
DEFAULT_CONFIG = {
    "version": 1,
    "min_sample_count": 50,
}


def _normalized_config(values: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (values or {}).get(ITEM_KEY)
    config = raw if isinstance(raw, dict) else {}
    try:
        minimum = int(config.get("min_sample_count"))
    except (TypeError, ValueError):
        minimum = int(settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT or 50)
    minimum = max(1, min(100000, minimum))
    return {
        "version": int(config.get("version") or 1),
        "min_sample_count": minimum,
    }


def get_config() -> dict[str, Any]:
    return _normalized_config(read_group_values(GROUP_KEY))


def get_min_sample_count() -> int:
    return int(get_config()["min_sample_count"])


def update_config(*, min_sample_count: int, updated_by: int | None = None) -> dict[str, Any]:
    try:
        minimum = int(min_sample_count)
    except (TypeError, ValueError):
        raise ValueError("min_sample_count 必须是整数") from None
    if minimum < 1 or minimum > 100000:
        raise ValueError("min_sample_count 必须在 1 到 100000 之间")
    current = get_config()
    next_config = {
        "version": int(current["version"]) + 1,
        "min_sample_count": minimum,
    }
    ok = write_group_values(
        GROUP_KEY,
        {ITEM_KEY: next_config},
        updated_by=updated_by,
        change_reason="update training admission minimum sample count",
    )
    if not ok:
        raise RuntimeError("训练准入配置保存失败")
    return next_config
