from __future__ import annotations

import json
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_system_config_group
from labelu.internal.adapter.persistence import crud_system_config_history
from labelu.internal.adapter.persistence import crud_system_config_item
from labelu.internal.adapter.persistence import crud_system_config_value
from labelu.internal.common.db import engine, session_scope
from labelu.internal.common.system_config_registry import (
    DEFAULT_ENV_NAME,
    GLOBAL_SCOPE_KEY,
    get_group_definition,
    list_group_keys,
)
from labelu.internal.domain.models.system_config_value import SystemConfigValue

_TABLE_NAMES = (
    "system_config_group",
    "system_config_item",
    "system_config_value",
    "system_config_history",
)
_MISSING = object()
_RUNTIME_OVERRIDES: dict[str, dict[str, Any]] = {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None) -> Any:
    if value in (None, ""):
        return None
    return json.loads(value)


def is_governance_store_ready() -> bool:
    try:
        inspector = inspect(engine)
        return all(inspector.has_table(name) for name in _TABLE_NAMES)
    except (SQLAlchemyError, OSError):
        return False


def set_runtime_override(group_key: str, item_key: str, value: Any) -> None:
    _RUNTIME_OVERRIDES.setdefault(group_key, {})[item_key] = value


def get_runtime_override(group_key: str, item_key: str) -> Any:
    return _RUNTIME_OVERRIDES.get(group_key, {}).get(item_key, _MISSING)


def clear_runtime_override(group_key: str, item_key: str | None = None) -> None:
    if item_key is None:
        _RUNTIME_OVERRIDES.pop(group_key, None)
        return
    group = _RUNTIME_OVERRIDES.get(group_key)
    if not group:
        return
    group.pop(item_key, None)
    if not group:
        _RUNTIME_OVERRIDES.pop(group_key, None)


def resolve_value(
    *,
    runtime_value: Any = _MISSING,
    env_value: Any = _MISSING,
    db_value: Any = _MISSING,
    default_value: Any = _MISSING,
) -> Any:
    for candidate in (runtime_value, env_value, db_value, default_value):
        if candidate is not _MISSING:
            return candidate
    return None


def _ensure_group_definition(db: Session, group_key: str) -> None:
    meta = get_group_definition(group_key)
    group = crud_system_config_group.get_by_key(db, group_key)
    if group is None:
        group = crud_system_config_group.create(
            db,
            group_key=group_key,
            group_name_zh=meta["group_name_zh"],
            group_comment=meta.get("group_comment"),
            scope_type=meta.get("scope_type") or "global",
            is_enabled=True,
            sort_order=int(meta.get("sort_order") or 0),
        )
    else:
        crud_system_config_group.update(
            db,
            group,
            group_name_zh=meta["group_name_zh"],
            group_comment=meta.get("group_comment"),
            scope_type=meta.get("scope_type") or "global",
            is_enabled=True,
            sort_order=int(meta.get("sort_order") or 0),
        )

    for item_meta in meta.get("items") or []:
        default_value = item_meta.get("default_value", None)
        row = crud_system_config_item.get_by_group_and_key(
            db,
            group_id=group.id,
            item_key=item_meta["item_key"],
        )
        payload = {
            "item_name_zh": item_meta["item_name_zh"],
            "item_comment": item_meta.get("item_comment"),
            "value_type": item_meta.get("value_type") or "json",
            "default_value_json": _json_dumps(default_value),
            "source_mode": item_meta.get("source_mode") or "db_primary",
            "env_var_name": item_meta.get("env_var_name"),
            "is_secret": bool(item_meta.get("is_secret")),
            "validation_rule_json": _json_dumps(item_meta.get("validation_rule"))
            if item_meta.get("validation_rule") is not None
            else None,
            "enum_options_json": _json_dumps(item_meta.get("enum_options"))
            if item_meta.get("enum_options") is not None
            else None,
            "scope_desc": item_meta.get("scope_desc"),
            "sort_order": int(item_meta.get("sort_order") or 0),
        }
        if row is None:
            crud_system_config_item.create(
                db,
                group_id=group.id,
                item_key=item_meta["item_key"],
                **payload,
            )
        else:
            crud_system_config_item.update(db, row, **payload)


def ensure_registry_synced(group_keys: list[str] | None = None) -> bool:
    if not is_governance_store_ready():
        return False
    keys = group_keys or list_group_keys()
    try:
        with session_scope() as db:
            with db.begin():
                for key in keys:
                    _ensure_group_definition(db, key)
        return True
    except (OperationalError, ProgrammingError, SQLAlchemyError):
        return False


def read_group_values(
    group_key: str,
    *,
    scope_key: str = GLOBAL_SCOPE_KEY,
    env_name: str = DEFAULT_ENV_NAME,
) -> dict[str, Any]:
    try:
        with session_scope() as db:
            group = crud_system_config_group.get_by_key(db, group_key)
            if group is None:
                return {}
            items = crud_system_config_item.list_by_group(db, group.id)
            if not items:
                return {}
            item_ids = [item.id for item in items]
            rows = (
                db.query(SystemConfigValue)
                .filter(
                    SystemConfigValue.item_id.in_(item_ids),
                    SystemConfigValue.scope_key == scope_key,
                    SystemConfigValue.env_name == env_name,
                    SystemConfigValue.is_active == True,
                )
                .all()
            )
            values_by_item = {row.item_id: row for row in rows}
            out: dict[str, Any] = {}
            for item in items:
                row = values_by_item.get(item.id)
                if row is None or not row.is_active:
                    continue
                try:
                    out[item.item_key] = _json_loads(row.value_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            return out
    except (OperationalError, ProgrammingError, SQLAlchemyError):
        return {}


def group_has_values(
    group_key: str,
    *,
    scope_key: str = GLOBAL_SCOPE_KEY,
    env_name: str = DEFAULT_ENV_NAME,
) -> bool:
    return bool(read_group_values(group_key, scope_key=scope_key, env_name=env_name))


def write_group_values(
    group_key: str,
    values: dict[str, Any],
    *,
    scope_key: str = GLOBAL_SCOPE_KEY,
    env_name: str = DEFAULT_ENV_NAME,
    value_source: str = "db",
    updated_by: int | None = None,
    change_reason: str | None = None,
    expected_revisions: dict[str, int] | None = None,
) -> bool:
    if not ensure_registry_synced([group_key]):
        raise RuntimeError(f"配置治理存储不可用：{group_key}")
    try:
        with session_scope() as db:
            with db.begin():
                group = crud_system_config_group.get_by_key(db, group_key)
                if group is None:
                    raise RuntimeError(f"配置组不存在：{group_key}")
                item_rows = {
                    row.item_key: row for row in crud_system_config_item.list_by_group(db, group.id)
                }
                unknown_keys = set(values).difference(item_rows)
                if unknown_keys:
                    unknown = ", ".join(sorted(unknown_keys))
                    raise ValueError(f"未知配置项：{unknown}")
                for item_key, value in values.items():
                    item = item_rows.get(item_key)
                    if item is None:
                        continue
                    after_json = _json_dumps(value)
                    current = (
                        db.query(SystemConfigValue)
                        .filter(
                            SystemConfigValue.item_id == item.id,
                            SystemConfigValue.scope_key == scope_key,
                            SystemConfigValue.env_name == env_name,
                        )
                        .with_for_update()
                        .first()
                    )
                    before_json = current.value_json if current is not None else None
                    if expected_revisions and item_key in expected_revisions:
                        expected_revision = int(expected_revisions[item_key])
                        revision_source = before_json
                        if revision_source is None:
                            revision_source = item.default_value_json
                        try:
                            revision_payload = _json_loads(revision_source)
                            actual_revision = int(revision_payload.get("revision"))
                        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                            actual_revision = 0
                        if actual_revision != expected_revision:
                            raise RuntimeError(
                                f"配置版本已变化：{group_key}.{item_key}"
                            )
                    if current is None:
                        crud_system_config_value.create(
                            db,
                            item_id=item.id,
                            scope_key=scope_key,
                            env_name=env_name,
                            value_json=after_json,
                            value_source=value_source,
                            is_active=True,
                            version_no=1,
                            updated_by=updated_by,
                        )
                        crud_system_config_history.create(
                            db,
                            item_id=item.id,
                            scope_key=scope_key,
                            env_name=env_name,
                            before_value_json=None,
                            after_value_json=after_json,
                            change_type="create",
                            change_reason=change_reason,
                            changed_by=updated_by,
                        )
                        continue
                    if before_json == after_json and current.is_active:
                        continue
                    next_version = int(current.version_no or 0) + 1
                    crud_system_config_value.update(
                        db,
                        current,
                        value_json=after_json,
                        value_source=value_source,
                        is_active=True,
                        version_no=next_version,
                        updated_by=updated_by,
                    )
                    crud_system_config_history.create(
                        db,
                        item_id=item.id,
                        scope_key=scope_key,
                        env_name=env_name,
                        before_value_json=before_json,
                        after_value_json=after_json,
                        change_type="update",
                        change_reason=change_reason,
                        changed_by=updated_by,
                    )
        return True
    except (OperationalError, ProgrammingError, SQLAlchemyError) as exc:
        raise RuntimeError(f"配置写入失败：{group_key}") from exc
