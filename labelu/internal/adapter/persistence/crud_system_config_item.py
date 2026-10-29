from typing import List, Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.system_config_item import SystemConfigItem


def create(
    db: Session,
    *,
    group_id: int,
    item_key: str,
    item_name_zh: str,
    item_comment: Optional[str] = None,
    value_type: str,
    default_value_json: Optional[str] = None,
    source_mode: str = "db_primary",
    env_var_name: Optional[str] = None,
    is_secret: bool = False,
    validation_rule_json: Optional[str] = None,
    enum_options_json: Optional[str] = None,
    scope_desc: Optional[str] = None,
    sort_order: int = 0,
) -> SystemConfigItem:
    row = SystemConfigItem(
        group_id=group_id,
        item_key=item_key,
        item_name_zh=item_name_zh,
        item_comment=item_comment,
        value_type=value_type,
        default_value_json=default_value_json,
        source_mode=source_mode,
        env_var_name=env_var_name,
        is_secret=is_secret,
        validation_rule_json=validation_rule_json,
        enum_options_json=enum_options_json,
        scope_desc=scope_desc,
        sort_order=sort_order,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, item_id: int) -> Optional[SystemConfigItem]:
    return db.query(SystemConfigItem).filter(SystemConfigItem.id == item_id).first()


def get_by_group_and_key(
    db: Session,
    *,
    group_id: int,
    item_key: str,
) -> Optional[SystemConfigItem]:
    return (
        db.query(SystemConfigItem)
        .filter(
            SystemConfigItem.group_id == group_id,
            SystemConfigItem.item_key == item_key,
        )
        .first()
    )


def list_by_group(db: Session, group_id: int) -> List[SystemConfigItem]:
    return (
        db.query(SystemConfigItem)
        .filter(SystemConfigItem.group_id == group_id)
        .order_by(SystemConfigItem.sort_order.asc(), SystemConfigItem.id.asc())
        .all()
    )


def update(db: Session, row: SystemConfigItem, **kwargs) -> SystemConfigItem:
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.flush()
    db.refresh(row)
    return row


def delete_row(db: Session, row: SystemConfigItem) -> None:
    db.delete(row)
    db.flush()
