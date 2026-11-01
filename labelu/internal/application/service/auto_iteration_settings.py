"""自动迭代训练策略的可视化配置服务。

配置写入系统配置治理存储（``auto_iteration`` 组），每次修改自动记录变更历史；
保存后立刻失效运行时缓存，使「下一轮判定」用上新值，无需重启服务。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_system_config_group,
    crud_system_config_history,
    crud_system_config_item,
)
from labelu.internal.common.auto_iteration_runtime import (
    AUTO_ITERATION_DEFAULTS,
    GROUP_KEY,
    METRIC_BASIS_OPTIONS,
    invalidate_auto_iteration_cache,
    load_auto_iteration_policy,
    validate_policy_values,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.system_config_provider import write_group_values
from labelu.internal.common.task_access import is_task_superuser

_METRIC_BASIS_LABELS = {
    "val_recall": "验证集召回率",
    "overall_recall": "整体加权召回率",
    "map50_95": "mAP50-95（历史口径）",
}


def _effective_values(policy: dict[str, Any]) -> Dict[str, Any]:
    return {key: policy[key] for key in AUTO_ITERATION_DEFAULTS}


def settings_payload(*, user: Any = None) -> dict[str, Any]:
    policy = load_auto_iteration_policy(force_refresh=True)
    return {
        "values": _effective_values(policy),
        "defaults": dict(AUTO_ITERATION_DEFAULTS),
        "sources": dict(policy.get("sources") or {}),
        "locked_keys": list(policy.get("locked_keys") or []),
        "can_edit": bool(is_task_superuser(user)) if user is not None else False,
        "max_iterations_after_first": int(
            policy.get("max_iterations_after_first") or 0
        ),
        "metric_basis_options": [
            {"value": key, "label": _METRIC_BASIS_LABELS.get(key, key)}
            for key in METRIC_BASIS_OPTIONS
        ],
        "threshold_hint": "达到该阈值即判定通过：停止迭代并允许进入编译",
        "max_rounds_hint": "该值包含首轮：4 = 首轮 + 3 次自动迭代",
    }


def _raise_validation(message: str, *, status_code: int = 422) -> None:
    raise LabelUException(
        ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=status_code,
        message=message,
    )


def update_settings(
    *,
    user: Any,
    values: Dict[str, Any],
    reason: str,
    expected_values: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    if not is_task_superuser(user):
        raise LabelUException(
            ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=403,
            message="只有管理员可以修改自动迭代策略",
        )
    reason = str(reason or "").strip()
    if not reason:
        _raise_validation("请填写修改原因")

    provided = {
        key: value
        for key, value in (values or {}).items()
        if key in AUTO_ITERATION_DEFAULTS and value is not None
    }
    if not provided:
        _raise_validation("没有提交任何需要修改的配置项")

    errors = validate_policy_values(provided)
    if errors:
        _raise_validation("；".join(errors))

    current = load_auto_iteration_policy(force_refresh=True)
    current_values = _effective_values(current)

    if expected_values:
        conflicts = [
            f"{key}（当前值 {current_values.get(key)!r}）"
            for key, expected in expected_values.items()
            if key in AUTO_ITERATION_DEFAULTS and current_values.get(key) != expected
        ]
        if conflicts:
            _raise_validation(
                "配置已被其他人修改，请刷新后重试：" + "、".join(conflicts),
                status_code=409,
            )

    locked_keys = set(current.get("locked_keys") or [])
    ignored_locked_keys = sorted(key for key in provided if key in locked_keys)
    writable = {key: value for key, value in provided.items() if key not in locked_keys}
    if writable:
        try:
            write_group_values(
                GROUP_KEY,
                writable,
                updated_by=int(getattr(user, "id", 0) or 0) or None,
                change_reason=reason,
            )
        except RuntimeError as exc:  # 治理存储不可用/版本冲突
            _raise_validation(str(exc), status_code=409)
        invalidate_auto_iteration_cache()

    payload = settings_payload(user=user)
    payload["updated_keys"] = sorted(writable)
    payload["ignored_locked_keys"] = ignored_locked_keys
    payload["previous_values"] = {
        key: current_values.get(key) for key in sorted(writable)
    }
    return payload


def _item_rows(db: Session) -> dict[str, Any]:
    group = crud_system_config_group.get_by_key(db, GROUP_KEY)
    if group is None:
        return {}
    return {
        item.item_key: item
        for item in crud_system_config_item.list_by_group(db, group.id)
    }


def _load_json(raw: Optional[str]) -> Any:
    if raw in (None, ""):
        return None
    import json

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def history_payload(
    db: Session, *, limit: int = 50, item_key: Optional[str] = None
) -> List[dict[str, Any]]:
    items = _item_rows(db)
    if not items:
        return []
    selected = {
        key: item for key, item in items.items() if item_key is None or key == item_key
    }
    rows: list[dict[str, Any]] = []
    for key, item in selected.items():
        for row in crud_system_config_history.list_by_item(db, item.id):
            rows.append(
                {
                    "id": int(row.id),
                    "item_key": key,
                    "before": _load_json(row.before_value_json),
                    "after": _load_json(row.after_value_json),
                    "change_type": row.change_type,
                    "change_reason": row.change_reason,
                    "changed_by": row.changed_by,
                    "changed_at": row.changed_at,
                }
            )
    rows.sort(
        key=lambda item: (
            item["changed_at"] or datetime.min,
            item["id"],
        ),
        reverse=True,
    )
    return rows[: max(1, min(int(limit), 200))]
