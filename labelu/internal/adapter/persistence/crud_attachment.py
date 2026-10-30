from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from labelu.internal.domain.models.attachment import TaskAttachment

def list_by(
    db: Session,
    size: int,
    ids: List[int] | None = [],
    task_id: Optional[int] = None,
    owner_id: Optional[int] = None,
    after: Optional[int] = None,
    before: Optional[int] = None,
    page: Optional[int] = None,
    sorting: Optional[str] = None,
    search: Optional[str] = None,
) -> Tuple[List[TaskAttachment], int]:
# query filter
    query_filter = [TaskAttachment.deleted_at == None, TaskAttachment.id.in_(ids)]
    if owner_id:
        query_filter.append(TaskAttachment.created_by == owner_id)
        
    if before:
        query_filter.append(TaskAttachment.id < before)
    if after:
        query_filter.append(TaskAttachment.id > after)
    if task_id:
        query_filter.append(TaskAttachment.task_id == task_id)

    keyword = (search or "").strip()
    if keyword:
        escaped = (
            keyword.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        search_filter = [TaskAttachment.filename.ilike(f"%{escaped}%", escape="\\")]
        if keyword.isdigit():
            search_filter.append(TaskAttachment.id == int(keyword))
        query_filter.append(or_(*search_filter))
        
    query = db.query(TaskAttachment).filter(*query_filter)

    # default order by id, before need select last items
    if before:
        query = query.order_by(TaskAttachment.id.desc())
    else:
        query = query.order_by(TaskAttachment.id.asc())
        
    count = query.count()
    
    results = (
        query.offset(offset=page * size if page else 0)
        .limit(limit=size)
        .all()
    )
    
    if sorting:
        field, order = sorting.split(":")
        if order == "desc":
            results = sorted(results, key=lambda x: getattr(x, field), reverse=True)
        else:
            results = sorted(results, key=lambda x: getattr(x, field))
    
    if before:
        results.reverse()
        
    return results, count

def create(db: Session, attachment: TaskAttachment) -> TaskAttachment:
    db.add(attachment)
    db.flush()
    db.refresh(attachment)
    return attachment


def get(db: Session, attachment_id: int) -> TaskAttachment:
    return (
        db.query(TaskAttachment)
        .filter(TaskAttachment.id == attachment_id, TaskAttachment.deleted_at == None)
        .first()
    )


def get_by_ids(db: Session, attachment_ids: List[int]) -> List[TaskAttachment]:
    return (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.id.in_(attachment_ids), TaskAttachment.deleted_at == None
        )
        .all()
    )


def delete(db: Session, attachment_ids: List[int]) -> None:
    db.query(TaskAttachment).filter(TaskAttachment.id.in_(attachment_ids)).update(
        {TaskAttachment.deleted_at: datetime.now()}
    )


def get_by_task_sha256(db: Session, task_id: int, sha256: str) -> TaskAttachment | None:
    """Return the active attachment with the same content in a task."""
    return (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.task_id == int(task_id),
            TaskAttachment.sha256 == sha256,
            TaskAttachment.deleted_at.is_(None),
        )
        .order_by(TaskAttachment.id.asc())
        .first()
    )


def list_soft_deleted_edge_validation_external_before(
    db: Session, *, deleted_before: datetime
) -> List[TaskAttachment]:
    return (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.deleted_at != None,
            TaskAttachment.deleted_at <= deleted_before,
            TaskAttachment.path.like("%/edge_validation_external/%"),
        )
        .order_by(TaskAttachment.id.asc())
        .all()
    )


def list_active_edge_validation_external(db: Session) -> List[TaskAttachment]:
    return (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.deleted_at == None,
            TaskAttachment.path.like("%/edge_validation_external/%"),
        )
        .order_by(TaskAttachment.id.asc())
        .all()
    )


def hard_delete_by_ids(
    db: Session, attachment_ids: List[int], task_id: int | None = None
) -> None:
    if not attachment_ids:
        return
    query = db.query(TaskAttachment).filter(TaskAttachment.id.in_(attachment_ids))
    if task_id is not None:
        query = query.filter(TaskAttachment.task_id == int(task_id))
    query.delete(synchronize_session=False)
