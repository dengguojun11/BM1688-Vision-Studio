from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.annotation_review import AnnotationReview
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator


def create(db: Session, row: AnnotationReview) -> AnnotationReview:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, review_id: int) -> AnnotationReview | None:
    return (
        db.query(AnnotationReview).filter(AnnotationReview.id == int(review_id)).first()
    )


def get_snapshot(
    db: Session, *, sample_id: int, annotation_sha256: str, source_type: str
) -> AnnotationReview | None:
    return (
        db.query(AnnotationReview)
        .filter(
            AnnotationReview.sample_id == int(sample_id),
            AnnotationReview.annotation_sha256 == annotation_sha256,
            AnnotationReview.source_type == source_type,
        )
        .first()
    )


def list_by(
    db: Session,
    *,
    page: int,
    size: int,
    user_id: int | None,
    status: str | None = None,
    search: str | None = None,
) -> tuple[list[AnnotationReview], int]:
    query = db.query(AnnotationReview).join(Task, Task.id == AnnotationReview.task_id)
    if user_id is not None:
        collaborator_exists = (
            db.query(TaskCollaborator.task_id)
            .filter(
                TaskCollaborator.task_id == Task.id,
                TaskCollaborator.user_id == int(user_id),
            )
            .exists()
        )
        query = query.filter(
            or_(
                Task.created_by == int(user_id),
                collaborator_exists,
                Task.source_type == "third_party",
            )
        )
    if status:
        query = query.filter(AnnotationReview.review_status == status.upper())
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(AnnotationReview.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(AnnotationReview.task_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(AnnotationReview.sample_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                AnnotationReview.annotation_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                AnnotationReview.source_type.ilike(pattern, escape=LIKE_ESCAPE),
                AnnotationReview.review_status.ilike(pattern, escape=LIKE_ESCAPE),
                AnnotationReview.review_reason.ilike(pattern, escape=LIKE_ESCAPE),
                Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                Task.description.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(AnnotationReview.updated_at.desc(), AnnotationReview.id.desc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return rows, total
