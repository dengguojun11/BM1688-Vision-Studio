from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, exists

from sqlalchemy.orm import Session, joinedload
from fastapi.encoders import jsonable_encoder

from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User
from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME


def create(db: Session, task: Task) -> Task:
    # A persistence helper must not infer tenant ownership.  Legacy callers
    # that intentionally create an unscoped task remain visible only to the
    # ownership audit/reconciliation flow; new business services must provide
    # an explicit project_id before calling this helper.
    db.add(task)
    db.flush()
    db.refresh(task)
    return task


def get_by_idempotency(
    db: Session, *, creator_id: int, idempotency_key: str
) -> Optional[Task]:
    return (
        db.query(Task)
        .filter(
            Task.created_by == int(creator_id),
            Task.idempotency_key == str(idempotency_key),
            Task.deleted_at.is_(None),
        )
        .first()
    )


def list_by(
    db: Session,
    owner_id: int,
    page: int = 0,
    size: int = 100,
    *,
    list_all_tasks: bool = False,
    status: Optional[str] = None,
    media_type: Optional[str] = None,
    training_ready: Optional[bool] = None,
    dataset_category_id: Optional[int] = None,
    uncategorized: bool = False,
    project_id: Optional[int] = None,
    dataset_id: Optional[int] = None,
) -> Tuple[List[Task], int]:
    query = visible_query(db, owner_id=owner_id, list_all_tasks=list_all_tasks)
    if status and str(status).strip():
        query = query.filter(Task.status == str(status).strip())
    if media_type and str(media_type).strip():
        query = query.filter(Task.media_type == str(media_type).strip())
    if training_ready is True:
        query = query.filter(Task.training_ready.is_(True))
    elif training_ready is False:
        query = query.filter(Task.training_ready.is_(False))
    if dataset_category_id is not None:
        query = query.filter(Task.dataset_category_id == int(dataset_category_id))
    elif uncategorized:
        query = query.filter(Task.dataset_category_id.is_(None))
    if project_id is not None:
        query = query.filter(Task.project_id == int(project_id))
    if dataset_id is not None:
        query = query.filter(Task.dataset_id == int(dataset_id))
    total = query.count()

    return (
        query.options(joinedload(Task.owner), joinedload(Task.dataset_category))
        .order_by(Task.id.desc())
        .offset(offset=page * size)
        .limit(limit=size)
        .all()
    ), total


def visible_query(db: Session, owner_id: int, *, list_all_tasks: bool = False):
    query = db.query(Task).filter(Task.deleted_at == None)
    is_builtin_admin = bool(
        db.query(User.id)
        .filter(
            User.id == int(owner_id),
            User.username == DEFAULT_ADMIN_USERNAME,
        )
        .first()
    )
    if not is_builtin_admin:
        # Consistent pools are internal administrator datasets. The source
        # field covers new pools; the marker fallback protects legacy rows
        # until reconciliation backfills their source metadata.
        query = query.filter(
            or_(
                Task.source_type.is_(None),
                Task.source_type != "consistent_pool",
            ),
            or_(
                Task.config.is_(None),
                ~Task.config.contains("train_filtered_consistent"),
            ),
        )
    if list_all_tasks:
        return query

    collaborator_exists = exists().where(
        TaskCollaborator.task_id == Task.id,
        TaskCollaborator.user_id == owner_id,
    )
    return query.filter(
        or_(
            Task.created_by == owner_id,
            collaborator_exists,
            # Third-party tasks are shared with all authenticated task users.
            Task.source_type == "third_party",
        ),
    )


def get(db: Session, task_id: int, lock: bool = False) -> Task:
    """return a Task item according to task id

    Args:
        db (Session): a session connect to table
        task_id (int): a given task id
        lock (bool, optional): if lock is True, means add exclusive lock in a row. Defaults to False.
    """
    if not lock:
        return (
            db.query(Task).filter(Task.id == task_id, Task.deleted_at == None).first()
        )
    else:
        return (
            db.query(Task)
            .filter(Task.id == task_id, Task.deleted_at == None)
            .with_for_update()
            .first()
        )


def update(db: Session, db_obj: Task, obj_in: Dict[str, Any]) -> Task:
    obj_data = jsonable_encoder(obj_in)
    for field in obj_data:
        if field in obj_in:
            setattr(db_obj, field, obj_in[field])
    db.add(db_obj)
    db.flush()
    db.refresh(db_obj)
    return db_obj


def delete(db: Session, db_obj: Task) -> Task:
    db_obj.deleted_at = datetime.now()
    db.add(db_obj)
    db.flush()
    db.refresh(db_obj)
    return db_obj


def count(db: Session, owner_id: int) -> int:
    return (
        db.query(Task)
        .filter(Task.created_by == owner_id, Task.deleted_at == None)
        .count()
    )
