from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern

from labelu.internal.domain.models.workflow import (
    WorkflowEvent,
    WorkflowInstance,
    WorkflowOutbox,
    WorkflowStep,
)


def get(db: Session, workflow_id: int) -> WorkflowInstance | None:
    return (
        db.query(WorkflowInstance)
        .filter(WorkflowInstance.id == int(workflow_id))
        .first()
    )


def get_by_key(db: Session, key: str) -> WorkflowInstance | None:
    return (
        db.query(WorkflowInstance)
        .filter(WorkflowInstance.idempotency_key == key)
        .first()
    )


def list_by_project(
    db: Session,
    project_id: int,
    page: int,
    size: int,
    status: str | None = None,
    search: str | None = None,
    workflow_type: str | None = None,
):
    query = db.query(WorkflowInstance).filter(
        WorkflowInstance.project_id == int(project_id)
    )
    if status:
        query = query.filter(WorkflowInstance.status == status.upper())
    normalized_workflow_type = str(workflow_type or "").strip()
    if normalized_workflow_type:
        query = query.filter(WorkflowInstance.workflow_type == normalized_workflow_type)
    query = _apply_search(query, search)
    total = query.count()
    return (
        query.order_by(WorkflowInstance.updated_at.desc(), WorkflowInstance.id.desc())
        .offset(page * size)
        .limit(size)
        .all(),
        total,
    )


def list_by_scope(
    db: Session,
    scope_key: str,
    page: int,
    size: int,
    status: str | None = None,
    search: str | None = None,
    created_by: int | None = None,
    workflow_type: str | None = None,
):
    query = db.query(WorkflowInstance).filter(
        WorkflowInstance.scope_key == str(scope_key)
    )
    if created_by is not None:
        query = query.filter(WorkflowInstance.created_by == int(created_by))
    if status:
        query = query.filter(WorkflowInstance.status == status.upper())
    normalized_workflow_type = str(workflow_type or "").strip()
    if normalized_workflow_type:
        query = query.filter(WorkflowInstance.workflow_type == normalized_workflow_type)
    query = _apply_search(query, search)
    total = query.count()
    return (
        query.order_by(WorkflowInstance.updated_at.desc(), WorkflowInstance.id.desc())
        .offset(page * size)
        .limit(size)
        .all(),
        total,
    )


def _apply_search(query, search: str | None):
    normalized = str(search or "").strip()
    if not normalized:
        return query
    pattern = literal_like_pattern(normalized)
    return query.filter(
        or_(
            cast(WorkflowInstance.id, String).ilike(pattern, escape=LIKE_ESCAPE),
            WorkflowInstance.scope_key.ilike(pattern, escape=LIKE_ESCAPE),
            WorkflowInstance.workflow_type.ilike(pattern, escape=LIKE_ESCAPE),
            WorkflowInstance.status.ilike(pattern, escape=LIKE_ESCAPE),
            WorkflowInstance.idempotency_key.ilike(pattern, escape=LIKE_ESCAPE),
            WorkflowInstance.current_step.ilike(pattern, escape=LIKE_ESCAPE),
            WorkflowInstance.last_error.ilike(pattern, escape=LIKE_ESCAPE),
        )
    )


def create(db: Session, row: WorkflowInstance) -> WorkflowInstance:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def add_step(db: Session, row: WorkflowStep) -> WorkflowStep:
    db.add(row)
    db.flush()
    return row


def add_event(db: Session, row: WorkflowEvent) -> WorkflowEvent:
    db.add(row)
    db.flush()
    return row


def add_outbox(db: Session, row: WorkflowOutbox) -> WorkflowOutbox:
    db.add(row)
    db.flush()
    return row


def get_outbox(db: Session, outbox_id: int) -> WorkflowOutbox | None:
    return db.query(WorkflowOutbox).filter(WorkflowOutbox.id == int(outbox_id)).first()


def list_outbox(
    db: Session, status: str | None = None, limit: int = 100
) -> list[WorkflowOutbox]:
    query = db.query(WorkflowOutbox)
    if status:
        query = query.filter(WorkflowOutbox.status == status.upper())
    return query.order_by(WorkflowOutbox.id.desc()).limit(limit).all()
