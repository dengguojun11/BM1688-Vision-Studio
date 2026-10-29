from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern

from labelu.internal.domain.models.auto_training import (
    AutoTrainingAuditEvent,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)


def get_plan(db: Session, plan_id: int) -> AutoTrainingPlan | None:
    return (
        db.query(AutoTrainingPlan)
        .filter(AutoTrainingPlan.id == plan_id, AutoTrainingPlan.deleted_at.is_(None))
        .first()
    )


def list_plans(
    db: Session,
    *,
    owner_id: int | None,
    page: int,
    size: int,
    project_id: int | None = None,
    search: str | None = None,
):
    query = db.query(AutoTrainingPlan).filter(AutoTrainingPlan.deleted_at.is_(None))
    if owner_id is not None:
        query = query.filter(AutoTrainingPlan.owner_id == owner_id)
    if project_id is not None:
        query = query.filter(AutoTrainingPlan.project_id == int(project_id))
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                AutoTrainingPlan.name.ilike(pattern, escape=LIKE_ESCAPE),
                AutoTrainingPlan.series_type.ilike(pattern, escape=LIKE_ESCAPE),
                AutoTrainingPlan.status.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(AutoTrainingPlan.updated_at.desc(), AutoTrainingPlan.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def get_version(db: Session, version_id: int) -> AutoTrainingPlanVersion | None:
    return db.query(AutoTrainingPlanVersion).filter(AutoTrainingPlanVersion.id == version_id).first()


def list_versions(db: Session, plan_id: int):
    return (
        db.query(AutoTrainingPlanVersion)
        .filter(AutoTrainingPlanVersion.plan_id == plan_id)
        .order_by(AutoTrainingPlanVersion.version_no.desc())
        .all()
    )


def list_audits(db: Session, plan_id: int):
    return (
        db.query(AutoTrainingAuditEvent)
        .filter(AutoTrainingAuditEvent.plan_id == plan_id)
        .order_by(AutoTrainingAuditEvent.created_at.desc(), AutoTrainingAuditEvent.id.desc())
        .all()
    )


def list_rounds(db: Session, plan_id: int):
    return (
        db.query(AutoTrainingRound)
        .filter(AutoTrainingRound.plan_id == plan_id)
        .order_by(AutoTrainingRound.round_no.desc())
        .all()
    )
