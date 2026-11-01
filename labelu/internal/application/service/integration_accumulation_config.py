from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.system_config_group import SystemConfigGroup
from labelu.internal.domain.models.system_config_item import SystemConfigItem
from labelu.internal.domain.models.system_config_value import SystemConfigValue
from labelu.internal.domain.models.user import User

GROUP_KEY = "third_party_accumulation"
ITEM_KEY = "min_sample_count"
SCOPE_KEY = "__global__"
DEFAULT_MIN_SAMPLE_COUNT = 50
MIN_ALLOWED = 1
MAX_ALLOWED = 100000


def _default_value() -> int:
    return int(settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT or DEFAULT_MIN_SAMPLE_COUNT)


def _find_item(db: Session) -> SystemConfigItem | None:
    return (
        db.query(SystemConfigItem)
        .join(SystemConfigGroup, SystemConfigGroup.id == SystemConfigItem.group_id)
        .filter(
            SystemConfigGroup.group_key == GROUP_KEY,
            SystemConfigItem.item_key == ITEM_KEY,
        )
        .first()
    )


def _find_active_value(db: Session, item_id: int) -> SystemConfigValue | None:
    return (
        db.query(SystemConfigValue)
        .filter(
            SystemConfigValue.item_id == int(item_id),
            SystemConfigValue.scope_key == SCOPE_KEY,
            SystemConfigValue.is_active.is_(True),
        )
        .order_by(SystemConfigValue.version_no.desc(), SystemConfigValue.id.desc())
        .first()
    )


def get_min_sample_count(db: Session) -> int:
    item = _find_item(db)
    if item is None:
        return _default_value()
    value = _find_active_value(db, int(item.id))
    if value is None:
        return _default_value()
    try:
        parsed = json.loads(value.value_json) if value.value_json else None
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    try:
        count = int(parsed)
    except (TypeError, ValueError):
        return _default_value()
    return max(MIN_ALLOWED, min(MAX_ALLOWED, count))


def get_config(db: Session) -> dict[str, Any]:
    item = _find_item(db)
    value = _find_active_value(db, int(item.id)) if item is not None else None
    return {
        "min_sample_count": get_min_sample_count(db),
        "default_min_sample_count": _default_value(),
        "min_allowed": MIN_ALLOWED,
        "max_allowed": MAX_ALLOWED,
        "source": "database" if value is not None else "environment_default",
        "updated_at": value.updated_at.isoformat() if value and value.updated_at else None,
        "updated_by": value.updated_by if value else None,
    }


def save_min_sample_count(
    db: Session, min_sample_count: int, current_user: User | None = None
) -> dict[str, Any]:
    count = int(min_sample_count)
    if count < MIN_ALLOWED or count > MAX_ALLOWED:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=422,
            message=f"累计张数必须在 {MIN_ALLOWED}~{MAX_ALLOWED} 之间",
        )
    with begin_transaction(db):
        group = (
            db.query(SystemConfigGroup)
            .filter(SystemConfigGroup.group_key == GROUP_KEY)
            .first()
        )
        if group is None:
            group = SystemConfigGroup(
                group_key=GROUP_KEY,
                group_name_zh="第三方回流累计配置",
                group_comment="控制第三方数据包进入任务前的最小累计图片数。",
                scope_type="global",
                is_enabled=True,
                sort_order=100,
            )
            db.add(group)
            db.flush()
        item = _find_item(db)
        if item is None:
            item = SystemConfigItem(
                group_id=int(group.id),
                item_key=ITEM_KEY,
                item_name_zh="最小累计图片数",
                item_comment="第三方数据包累计达到该数量后才创建正式任务。",
                value_type="integer",
                default_value_json=json.dumps(_default_value()),
                source_mode="db_primary",
                is_secret=False,
                validation_rule_json=json.dumps(
                    {"min": MIN_ALLOWED, "max": MAX_ALLOWED}
                ),
                scope_desc="全局",
                sort_order=10,
            )
            db.add(item)
            db.flush()
        value = _find_active_value(db, int(item.id))
        if value is None:
            value = SystemConfigValue(
                item_id=int(item.id),
                scope_key=SCOPE_KEY,
                env_name="default",
                value_json=json.dumps(count),
                value_source="db",
                is_active=True,
                version_no=1,
                updated_by=int(current_user.id) if current_user else None,
            )
            db.add(value)
        else:
            value.value_json = json.dumps(count)
            value.version_no = int(value.version_no or 0) + 1
            value.updated_by = int(current_user.id) if current_user else None
        db.flush()
    return get_config(db)
