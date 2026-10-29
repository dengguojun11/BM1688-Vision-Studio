from typing import List, Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.system_config_group import SystemConfigGroup


def create(
    db: Session,
    *,
    group_key: str,
    group_name_zh: str,
    group_comment: Optional[str] = None,
    scope_type: str = "global",
    is_enabled: bool = True,
    sort_order: int = 0,
) -> SystemConfigGroup:
    row = SystemConfigGroup(
        group_key=group_key,
        group_name_zh=group_name_zh,
        group_comment=group_comment,
        scope_type=scope_type,
        is_enabled=is_enabled,
        sort_order=sort_order,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, group_id: int) -> Optional[SystemConfigGroup]:
    return db.query(SystemConfigGroup).filter(SystemConfigGroup.id == group_id).first()


def get_by_key(db: Session, group_key: str) -> Optional[SystemConfigGroup]:
    return (
        db.query(SystemConfigGroup)
        .filter(SystemConfigGroup.group_key == group_key)
        .first()
    )


def list_all(db: Session, *, enabled_only: bool = False) -> List[SystemConfigGroup]:
    q = db.query(SystemConfigGroup)
    if enabled_only:
        q = q.filter(SystemConfigGroup.is_enabled == True)
    return q.order_by(SystemConfigGroup.sort_order.asc(), SystemConfigGroup.id.asc()).all()


def update(db: Session, row: SystemConfigGroup, **kwargs) -> SystemConfigGroup:
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.flush()
    db.refresh(row)
    return row


def delete_row(db: Session, row: SystemConfigGroup) -> None:
    db.delete(row)
    db.flush()
