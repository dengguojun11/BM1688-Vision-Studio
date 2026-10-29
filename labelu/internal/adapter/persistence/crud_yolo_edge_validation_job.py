from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import String, cast, desc, exists, func, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


def create(
    db: Session,
    *,
    user_id: int,
    task_id: int,
    export_id: int,
    mode: str,
    threshold_source: str | None,
    sample_ids_json: str,
    thresholds_json: str,
    ai_thresholds_json: str | None,
    image_count: int,
    labeled_image_count: int,
    unlabeled_image_count: int,
    result_json: str | None = None,
    report_summary: str | None = None,
) -> YoloEdgeValidationJob:
    row = YoloEdgeValidationJob(
        user_id=user_id,
        task_id=task_id,
        export_id=export_id,
        status="PENDING",
        mode=mode,
        threshold_source=threshold_source,
        sample_ids_json=sample_ids_json,
        thresholds_json=thresholds_json,
        ai_thresholds_json=ai_thresholds_json,
        image_count=int(image_count),
        labeled_image_count=int(labeled_image_count),
        unlabeled_image_count=int(unlabeled_image_count),
        result_json=result_json,
        report_summary=report_summary,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, job_id: int) -> Optional[YoloEdgeValidationJob]:
    return (
        db.query(YoloEdgeValidationJob)
        .filter(YoloEdgeValidationJob.id == job_id)
        .first()
    )


def get_latest_for_export(
    db: Session, *, export_id: int
) -> Optional[YoloEdgeValidationJob]:
    return (
        db.query(YoloEdgeValidationJob)
        .filter(YoloEdgeValidationJob.export_id == int(export_id))
        .order_by(desc(YoloEdgeValidationJob.id))
        .limit(1)
        .first()
    )


def latest_by_export_ids(
    db: Session, *, export_ids: Iterable[int]
) -> Dict[int, YoloEdgeValidationJob]:
    normalized_ids = sorted({int(export_id) for export_id in export_ids if export_id})
    if not normalized_ids:
        return {}
    latest_ids = (
        db.query(func.max(YoloEdgeValidationJob.id).label("id"))
        .filter(YoloEdgeValidationJob.export_id.in_(normalized_ids))
        .group_by(YoloEdgeValidationJob.export_id)
        .subquery()
    )
    rows = (
        db.query(YoloEdgeValidationJob)
        .join(latest_ids, YoloEdgeValidationJob.id == latest_ids.c.id)
        .all()
    )
    return {int(row.export_id): row for row in rows}


def update(db: Session, row: YoloEdgeValidationJob, **kwargs) -> YoloEdgeValidationJob:
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def has_active_for_export(db: Session, *, export_id: int) -> bool:
    return (
        db.query(YoloEdgeValidationJob)
        .filter(
            YoloEdgeValidationJob.export_id == int(export_id),
            # ``QUEUED`` is the persisted state used by the newer automatic
            # dispatch path.  It is still an active job for export-level
            # duplicate protection, just like the historical ``PENDING``
            # state and an already assigned ``PROCESSING`` row.
            YoloEdgeValidationJob.status.in_(("PENDING", "QUEUED", "PROCESSING")),
        )
        .first()
        is not None
    )


def get_latest_active_for_export(
    db: Session, *, export_id: int
) -> Optional[YoloEdgeValidationJob]:
    return (
        db.query(YoloEdgeValidationJob)
        .filter(
            YoloEdgeValidationJob.export_id == int(export_id),
            YoloEdgeValidationJob.status.in_(("PENDING", "QUEUED", "PROCESSING")),
        )
        .order_by(desc(YoloEdgeValidationJob.id))
        .first()
    )


def delete_all_for_export(db: Session, *, export_id: int) -> int:
    n = (
        db.query(YoloEdgeValidationJob)
        .filter(YoloEdgeValidationJob.export_id == int(export_id))
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n)


def query_for_user(
    db: Session,
    user_id: int,
    *,
    export_id: int | None = None,
    task_id: int | None = None,
    keyword: str | None = None,
    status: str | None = None,
    edge_device_id: str | None = None,
    source_type: str | None = None,
    source_system: str | None = None,
    pipeline_origin: str | None = None,
    active_only: bool = False,
    task_access_unrestricted: bool = False,
):
    """Build the scoped queue query without applying pagination.

    The queue summary and the paginated task-center endpoint must use the
    exact same task-visibility predicate.  Keeping it here avoids a subtle
    mismatch where a user can see a row in one endpoint but not the other.
    """
    base_filters = [
        Task.deleted_at == None,
        Task.media_type == MediaType.IMAGE.value,
    ]
    if not task_access_unrestricted:
        collaborator_exists = exists().where(
            TaskCollaborator.task_id == Task.id,
            TaskCollaborator.user_id == user_id,
        )
        base_filters.append(or_(Task.created_by == user_id, collaborator_exists))
    q = (
        db.query(YoloEdgeValidationJob, Task)
        .join(Task, Task.id == YoloEdgeValidationJob.task_id)
        .filter(*base_filters)
    )
    if export_id is not None:
        q = q.filter(YoloEdgeValidationJob.export_id == int(export_id))
    if task_id is not None:
        q = q.filter(YoloEdgeValidationJob.task_id == int(task_id))
    if keyword and str(keyword).strip():
        needle = literal_like_pattern(str(keyword).strip())
        q = q.filter(
            or_(
                cast(YoloEdgeValidationJob.id, String).ilike(
                    needle, escape=LIKE_ESCAPE
                ),
                cast(YoloEdgeValidationJob.task_id, String).ilike(
                    needle, escape=LIKE_ESCAPE
                ),
                Task.name.ilike(needle, escape=LIKE_ESCAPE),
                YoloEdgeValidationJob.edge_device_id.ilike(
                    needle, escape=LIKE_ESCAPE
                ),
                YoloEdgeValidationJob.edge_device_name.ilike(
                    needle, escape=LIKE_ESCAPE
                ),
                YoloEdgeValidationJob.source_external_task_id.ilike(
                    needle, escape=LIKE_ESCAPE
                ),
                YoloEdgeValidationJob.source_external_package_id.ilike(
                    needle, escape=LIKE_ESCAPE
                ),
            )
        )
    if status and str(status).strip():
        # Persisted job states are uppercase.  Normalize here so the legacy
        # endpoint and the queue center agree when callers send ``processing``
        # or another lower-case status value.
        q = q.filter(YoloEdgeValidationJob.status == str(status).strip().upper())
    if edge_device_id and str(edge_device_id).strip():
        q = q.filter(
            YoloEdgeValidationJob.edge_device_id == str(edge_device_id).strip()
        )
    if source_type and str(source_type).strip():
        q = q.filter(YoloEdgeValidationJob.source_type == str(source_type).strip())
    if source_system and str(source_system).strip():
        q = q.filter(YoloEdgeValidationJob.source_system == str(source_system).strip())
    if pipeline_origin and str(pipeline_origin).strip():
        q = q.filter(
            YoloEdgeValidationJob.pipeline_origin == str(pipeline_origin).strip()
        )
    if active_only:
        q = q.filter(
            YoloEdgeValidationJob.status.in_(("PENDING", "QUEUED", "PROCESSING"))
        )
    return q


def list_for_user(
    db: Session,
    user_id: int,
    *,
    export_id: int | None = None,
    task_id: int | None = None,
    keyword: str | None = None,
    status: str | None = None,
    edge_device_id: str | None = None,
    source_type: str | None = None,
    source_system: str | None = None,
    pipeline_origin: str | None = None,
    active_only: bool = False,
    page: int = 0,
    size: int = 20,
    task_access_unrestricted: bool = False,
) -> Tuple[List[Tuple[YoloEdgeValidationJob, Task]], int]:
    q = query_for_user(
        db,
        user_id,
        export_id=export_id,
        task_id=task_id,
        keyword=keyword,
        status=status,
        edge_device_id=edge_device_id,
        source_type=source_type,
        source_system=source_system,
        pipeline_origin=pipeline_origin,
        active_only=active_only,
        task_access_unrestricted=task_access_unrestricted,
    )
    total = q.count()
    rows = (
        q.order_by(desc(YoloEdgeValidationJob.id)).offset(page * size).limit(size).all()
    )
    return rows, total
