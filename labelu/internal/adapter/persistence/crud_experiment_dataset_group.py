from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.domain.models.experiment_dataset_group import (
    ExperimentDatasetGroup,
)


def get_by_key(
    db: Session, *, aggregation_key: str, lock: bool = False
) -> ExperimentDatasetGroup | None:
    query = db.query(ExperimentDatasetGroup).filter(
        ExperimentDatasetGroup.aggregation_key == aggregation_key
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def get(db: Session, *, group_id: int, lock: bool = False) -> ExperimentDatasetGroup | None:
    query = db.query(ExperimentDatasetGroup).filter(
        ExperimentDatasetGroup.id == group_id
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def create(db: Session, row: ExperimentDatasetGroup) -> ExperimentDatasetGroup:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def update(
    db: Session, *, row: ExperimentDatasetGroup, obj_in: dict[str, Any]
) -> ExperimentDatasetGroup:
    for key, value in obj_in.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row
