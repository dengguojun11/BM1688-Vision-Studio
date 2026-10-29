from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.edge_feedback import EdgeHardExample, InferenceEvent
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.project import Project, ProjectMember


def event_by_key(db: Session, project_id: int | None, event_key: str, scope_key: str = "global"):
    query = db.query(InferenceEvent).filter(InferenceEvent.event_key == event_key)
    if project_id is None:
        query = query.filter(InferenceEvent.scope_key == scope_key)
    else:
        query = query.filter(InferenceEvent.project_id == int(project_id))
    return query.first()


def hard_by_fingerprint(db: Session, project_id: int | None, fingerprint: str, scope_key: str = "global"):
    query = db.query(EdgeHardExample).filter(EdgeHardExample.fingerprint == fingerprint)
    if project_id is None:
        query = query.filter(EdgeHardExample.scope_key == scope_key)
    else:
        query = query.filter(EdgeHardExample.project_id == int(project_id))
    return query.first()


def hard_by_event(db: Session, inference_event_id: int):
    return db.query(EdgeHardExample).filter_by(inference_event_id=inference_event_id).first()


def create_event(db: Session, row: InferenceEvent):
    db.add(row); db.flush(); db.refresh(row); return row


def create_hard(db: Session, row: EdgeHardExample):
    db.add(row); db.flush(); db.refresh(row); return row


def get_hard(db: Session, hard_id: int):
    return db.query(EdgeHardExample).filter_by(id=hard_id).first()


def list_hard(
    db: Session,
    project_id: int | None,
    page: int,
    size: int,
    status: str | None,
    *,
    search: str | None = None,
    scope_key: str | None = "global",
    creator_id: int | None = None,
    unrestricted: bool = False,
):
    if project_id is None:
        query = db.query(EdgeHardExample)
        if scope_key is not None:
            query = query.filter(EdgeHardExample.scope_key == scope_key)
        if not unrestricted and creator_id is not None:
            membership = (
                db.query(ProjectMember.project_id)
                .filter(
                    ProjectMember.project_id == EdgeHardExample.project_id,
                    ProjectMember.user_id == int(creator_id),
                    ProjectMember.status == "ACTIVE",
                )
                .exists()
            )
            query = (
                query.outerjoin(Project, Project.id == EdgeHardExample.project_id)
                .outerjoin(ModelVersion, ModelVersion.id == EdgeHardExample.model_version_id)
                .filter(
                    or_(
                        EdgeHardExample.created_by == int(creator_id),
                        Project.owner_id == int(creator_id),
                        ModelVersion.created_by == int(creator_id),
                        membership,
                    )
                )
            )
    else:
        query = db.query(EdgeHardExample).filter(EdgeHardExample.project_id == int(project_id))
    if status:
        query = query.filter(EdgeHardExample.review_status == status.upper())
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(EdgeHardExample.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                EdgeHardExample.device_id.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeHardExample.fingerprint.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeHardExample.asset_uri.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeHardExample.hard_example_type.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                EdgeHardExample.review_status.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeHardExample.training_status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(EdgeHardExample.model_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(EdgeHardExample.inference_event_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    total = query.count()
    return query.order_by(EdgeHardExample.created_at.desc(), EdgeHardExample.id.desc()).offset(page * size).limit(size).all(), total
