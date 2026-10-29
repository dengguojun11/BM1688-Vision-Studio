from __future__ import annotations

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup


def get_group(db: Session, group_key: str) -> AlgorithmGroup | None:
    return (
        db.query(AlgorithmGroup)
        .filter(AlgorithmGroup.group_key == str(group_key).strip().lower())
        .first()
    )


def get_group_by_id(db: Session, group_id: int) -> AlgorithmGroup | None:
    return db.query(AlgorithmGroup).filter(AlgorithmGroup.id == int(group_id)).first()


def list_groups(
    db: Session,
    status: str | None = "ACTIVE",
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> list[AlgorithmGroup] | tuple[list[AlgorithmGroup], int]:
    query = db.query(AlgorithmGroup)
    if status:
        query = query.filter(AlgorithmGroup.status == status)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(AlgorithmGroup.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmGroup.group_key.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmGroup.name.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmGroup.description.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    query = query.order_by(AlgorithmGroup.sort_order.asc(), AlgorithmGroup.id.asc())
    if page is None or size is None:
        return query.all()
    normalized_page = max(0, int(page))
    normalized_size = max(1, int(size))
    total = query.count()
    rows = query.offset(normalized_page * normalized_size).limit(normalized_size).all()
    return rows, total


def create_group(db: Session, row: AlgorithmGroup) -> AlgorithmGroup:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_definition(db: Session, algorithm_key: str) -> AlgorithmDefinition | None:
    return (
        db.query(AlgorithmDefinition)
        .filter(AlgorithmDefinition.algorithm_key == str(algorithm_key).strip().lower())
        .first()
    )


def get_definition_by_id(db: Session, algorithm_id: int) -> AlgorithmDefinition | None:
    return db.query(AlgorithmDefinition).filter(AlgorithmDefinition.id == int(algorithm_id)).first()


def lock_definition(db: Session, algorithm_id: int) -> AlgorithmDefinition | None:
    """Lock the algorithm namespace while allocating a model version number."""
    return (
        db.query(AlgorithmDefinition)
        .filter(AlgorithmDefinition.id == int(algorithm_id))
        .with_for_update()
        .first()
    )


def list_definitions(
    db: Session,
    *,
    status: str | None = "ACTIVE",
    task_type: str | None = None,
    group_key: str | None = None,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> list[AlgorithmDefinition] | tuple[list[AlgorithmDefinition], int]:
    query = db.query(AlgorithmDefinition)
    if status:
        query = query.filter(AlgorithmDefinition.status == status)
    if task_type:
        query = query.filter(AlgorithmDefinition.task_type == task_type)
    if group_key:
        query = query.join(AlgorithmGroup).filter(
            AlgorithmGroup.group_key == str(group_key).strip().lower()
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        if not group_key:
            query = query.outerjoin(AlgorithmGroup)
        query = query.filter(
            or_(
                cast(AlgorithmDefinition.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmDefinition.algorithm_key.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmDefinition.name.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmDefinition.description.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmDefinition.task_type.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmDefinition.training_adapter.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmDefinition.algorithm_kind.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmGroup.group_key.ilike(pattern, escape=LIKE_ESCAPE),
                AlgorithmGroup.name.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    query = query.order_by(AlgorithmDefinition.name.asc(), AlgorithmDefinition.id.asc())
    if page is None or size is None:
        return query.all()
    total = query.count()
    rows = query.offset(max(0, int(page)) * max(1, int(size))).limit(max(1, int(size))).all()
    return rows, total


def create_definition(db: Session, row: AlgorithmDefinition) -> AlgorithmDefinition:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row
