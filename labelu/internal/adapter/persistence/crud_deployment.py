from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session, joinedload

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern

from labelu.internal.domain.models.deployment import (
    DeploymentObservation,
    DeploymentRelease,
    DeploymentTarget,
    DeploymentTransitionAudit,
)


def create_release(db: Session, row: DeploymentRelease) -> DeploymentRelease:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def create_target(db: Session, row: DeploymentTarget) -> DeploymentTarget:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def create_observation(db: Session, row: DeploymentObservation) -> DeploymentObservation:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def create_transition_audit(
    db: Session, row: DeploymentTransitionAudit
) -> DeploymentTransitionAudit:
    """Insert one immutable deployment transition event."""
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_transition_audit_by_idempotency(
    db: Session, idempotency_key: str
) -> DeploymentTransitionAudit | None:
    if not idempotency_key:
        return None
    return (
        db.query(DeploymentTransitionAudit)
        .filter(DeploymentTransitionAudit.idempotency_key == str(idempotency_key))
        .first()
    )


def list_transition_audits(
    db: Session,
    release_id: int,
    *,
    target_id: int | None = None,
    actor_type: str | None = None,
    actor_id: int | None = None,
    agent_id: str | None = None,
    operation: str | None = None,
    created_after=None,
    created_before=None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[DeploymentTransitionAudit], int]:
    query = db.query(DeploymentTransitionAudit).filter(
        DeploymentTransitionAudit.release_id == int(release_id)
    )
    if target_id is not None:
        query = query.filter(DeploymentTransitionAudit.target_id == int(target_id))
    if actor_type:
        query = query.filter(DeploymentTransitionAudit.actor_type == str(actor_type).upper())
    if actor_id is not None:
        query = query.filter(DeploymentTransitionAudit.actor_id == int(actor_id))
    if agent_id:
        query = query.filter(DeploymentTransitionAudit.agent_id == str(agent_id))
    if operation:
        query = query.filter(DeploymentTransitionAudit.operation == str(operation).lower())
    if created_after is not None:
        query = query.filter(DeploymentTransitionAudit.created_at >= created_after)
    if created_before is not None:
        query = query.filter(DeploymentTransitionAudit.created_at <= created_before)
    total = query.count()
    rows = (
        query.order_by(
            DeploymentTransitionAudit.created_at.desc(),
            DeploymentTransitionAudit.id.desc(),
        )
        .offset(max(0, int(offset)))
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    return rows, total


def get_release(db: Session, release_id: int) -> DeploymentRelease | None:
    return (
        db.query(DeploymentRelease)
        .options(joinedload(DeploymentRelease.targets).joinedload(DeploymentTarget.observations))
        .filter(DeploymentRelease.id == int(release_id))
        .first()
    )


def list_releases(
    db: Session,
    project_id: int,
    page: int,
    size: int,
    status: str | None = None,
    search: str | None = None,
) -> tuple[list[DeploymentRelease], int]:
    query = db.query(DeploymentRelease).filter(DeploymentRelease.project_id == int(project_id))
    if status:
        query = query.filter(DeploymentRelease.status == str(status).upper())
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                cast(DeploymentRelease.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                DeploymentRelease.release_key.ilike(pattern, escape=LIKE_ESCAPE),
                DeploymentRelease.status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(DeploymentRelease.model_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                DeploymentRelease.target_device_ids_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    total = query.count()
    rows = (
        query.options(joinedload(DeploymentRelease.targets).joinedload(DeploymentTarget.observations))
        .order_by(DeploymentRelease.created_at.desc(), DeploymentRelease.id.desc())
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, int(size)))
        .all()
    )
    return rows, total


def get_target(db: Session, release_id: int, device_id: str) -> DeploymentTarget | None:
    return (
        db.query(DeploymentTarget)
        .filter(
            DeploymentTarget.release_id == int(release_id),
            DeploymentTarget.device_id == str(device_id),
        )
        .first()
    )


def get_observation_by_key(
    db: Session, target_id: int, observation_key: str
) -> DeploymentObservation | None:
    return (
        db.query(DeploymentObservation)
        .filter(
            DeploymentObservation.target_id == int(target_id),
            DeploymentObservation.observation_key == str(observation_key),
        )
        .first()
    )


def list_observations(db: Session, target_id: int, *, limit: int = 100) -> list[DeploymentObservation]:
    return (
        db.query(DeploymentObservation)
        .filter(DeploymentObservation.target_id == int(target_id))
        .order_by(DeploymentObservation.sampled_at.desc(), DeploymentObservation.id.desc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
