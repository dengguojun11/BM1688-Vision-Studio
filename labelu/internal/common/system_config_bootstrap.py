from __future__ import annotations

import json
from typing import Any, Callable

from labelu.internal.common.system_config_provider import (
    group_has_values,
    is_governance_store_ready,
    write_group_values,
)


def _load_bootstrap_specs() -> dict[str, tuple[Callable[[], dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]]]]:
    from labelu.internal.common.strategy_integration_s3_config import (
        _legacy_store_to_db_items as _legacy_s3_store_to_db_items,
        load_s3_connection_store_legacy,
    )
    from labelu.internal.common.ai_labeling_runtime import (
        _legacy_runtime_to_db_items,
        load_ai_labeling_runtime_legacy,
    )
    from labelu.internal.common.bm1688_compiler_config import (
        _legacy_bm1688_config_to_db_items,
        load_bm1688_compiler_config_legacy,
    )
    from labelu.internal.common.remote_train_nodes_config import (
        _legacy_remote_train_nodes_to_db_items,
        load_remote_train_nodes_config_legacy,
    )
    from labelu.internal.common.yolo_eval_prompts import (
        _legacy_prompts_to_db_items,
        load_yolo_eval_prompts_legacy,
    )
    from labelu.internal.common.yolo_quality_policy_store import (
        _legacy_quality_policy_store_to_db_items,
        load_quality_policy_store_legacy,
    )

    return {
        "ai_labeling_runtime": (load_ai_labeling_runtime_legacy, _legacy_runtime_to_db_items),
        "yolo_eval_prompts": (load_yolo_eval_prompts_legacy, _legacy_prompts_to_db_items),
        "remote_train_nodes": (
            load_remote_train_nodes_config_legacy,
            _legacy_remote_train_nodes_to_db_items,
        ),
        "bm1688_compiler_config": (
            load_bm1688_compiler_config_legacy,
            _legacy_bm1688_config_to_db_items,
        ),
        "yolo_quality_policy_store": (
            load_quality_policy_store_legacy,
            _legacy_quality_policy_store_to_db_items,
        ),
        "strategy_integration_s3": (
            load_s3_connection_store_legacy,
            _legacy_s3_store_to_db_items,
        ),
    }


def bootstrap_group(group_key: str, *, overwrite: bool = False) -> dict[str, Any]:
    specs = _load_bootstrap_specs()
    if group_key not in specs:
        raise KeyError(f"unknown system config group: {group_key}")
    if not is_governance_store_ready():
        return {"group_key": group_key, "status": "store_not_ready"}
    if not overwrite and group_has_values(group_key):
        return {"group_key": group_key, "status": "skipped_existing"}
    loader, encoder = specs[group_key]
    raw = loader()
    ok = write_group_values(
        group_key,
        encoder(raw),
        value_source="bootstrap",
        change_reason="bootstrap legacy json into governance tables",
    )
    return {
        "group_key": group_key,
        "status": "ok" if ok else "write_failed",
    }


def bootstrap_all_legacy_system_configs(*, overwrite: bool = False) -> dict[str, Any]:
    results = [bootstrap_group(group_key, overwrite=overwrite) for group_key in _load_bootstrap_specs()]
    return {
        "ok": all(item.get("status") in {"ok", "skipped_existing"} for item in results),
        "results": results,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            bootstrap_all_legacy_system_configs(),
            ensure_ascii=False,
            indent=2,
        )
    )
