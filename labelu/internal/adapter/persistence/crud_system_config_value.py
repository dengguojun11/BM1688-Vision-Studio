from typing import List, Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.system_config_value import SystemConfigValue


def create(
    db: Session,
    *,
    item_id: int,
    scope_key: str = "__global__",
    env_name: str = "default",
    value_json: Optional[str] = None,
    value_source: str = "db",
    is_active: bool = True,
    version_no: int = 1,
    updated_by: Optional[int] = None,
) -> SystemConfigValue:
    row = SystemConfigValue(
        item_id=item_id,
        scope_key=scope_key,
        env_name=env_name,
        value_json=value_json,
        value_source=value_source,
        is_active=is_active,
        version_no=version_no,
        updated_by=updated_by,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, value_id: int) -> Optional[SystemConfigValue]:
    return db.query(SystemConfigValue).filter(SystemConfigValue.id == value_id).first()


def get_by_scope(
    db: Session,
    *,
    item_id: int,
    scope_key: str = "__global__",
    env_name: str = "default",
) -> Optional[SystemConfigValue]:
    return (
        db.query(SystemConfigValue)
        .filter(
            SystemConfigValue.item_id == item_id,
            SystemConfigValue.scope_key == scope_key,
            SystemConfigValue.env_name == env_name,
        )
        .first()
    )


def list_by_item(db: Session, item_id: int) -> List[SystemConfigValue]:
    return (
        db.query(SystemConfigValue)
        .filter(SystemConfigValue.item_id == item_id)
        .order_by(SystemConfigValue.id.asc())
        .all()
    )


def update(db: Session, row: SystemConfigValue, **kwargs) -> SystemConfigValue:
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.flush()
    db.refresh(row)
    return row


def delete_row(db: Session, row: SystemConfigValue) -> None:
    db.delete(row)
    db.flush()
