from typing import List, Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.system_config_history import SystemConfigHistory


def create(
    db: Session,
    *,
    item_id: int,
    scope_key: str = "__global__",
    env_name: str = "default",
    before_value_json: Optional[str] = None,
    after_value_json: Optional[str] = None,
    change_type: str = "update",
    change_reason: Optional[str] = None,
    changed_by: Optional[int] = None,
) -> SystemConfigHistory:
    row = SystemConfigHistory(
        item_id=item_id,
        scope_key=scope_key,
        env_name=env_name,
        before_value_json=before_value_json,
        after_value_json=after_value_json,
        change_type=change_type,
        change_reason=change_reason,
        changed_by=changed_by,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, history_id: int) -> Optional[SystemConfigHistory]:
    return (
        db.query(SystemConfigHistory)
        .filter(SystemConfigHistory.id == history_id)
        .first()
    )


def list_by_item(db: Session, item_id: int) -> List[SystemConfigHistory]:
    return (
        db.query(SystemConfigHistory)
        .filter(SystemConfigHistory.item_id == item_id)
        .order_by(SystemConfigHistory.changed_at.desc(), SystemConfigHistory.id.desc())
        .all()
    )
