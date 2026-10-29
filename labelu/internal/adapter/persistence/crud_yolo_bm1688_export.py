from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import String, cast, desc, exists, func, or_
from sqlalchemy.orm import Session, load_only

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.common.task_access import task_visibility_clause


def create(
    db: Session,
    *,
    user_id: int,
    task_id: int,
    yolo_train_job_id: int,
    imgsz: int,
    worker_action: str = "export",
) -> YoloBm1688Export:
    row = YoloBm1688Export(
        user_id=user_id,
        task_id=task_id,
        yolo_train_job_id=yolo_train_job_id,
        status="PENDING",
        imgsz=int(imgsz),
        worker_action=str(worker_action or "export"),
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, export_id: int) -> Optional[YoloBm1688Export]:
    return db.query(YoloBm1688Export).filter(YoloBm1688Export.id == export_id).first()


def get_for_update(db: Session, export_id: int) -> Optional[YoloBm1688Export]:
    return (
        db.query(YoloBm1688Export)
        .filter(YoloBm1688Export.id == int(export_id))
        .with_for_update()
        .first()
    )


def claim_publish(
    db: Session,
    *,
    export_id: int,
    job_id: str,
    lease_owner: str,
    lease_seconds: int,
) -> Tuple[Optional[YoloBm1688Export], str]:
    """Atomically claim one export for AICM publication.

    The row lock is effective on MySQL and the write transaction serializes
    competing claimers on SQLite.  A completed publication is immutable from
    the claimer's perspective; a failed or expired claim may be retried.
    """
    row = get_for_update(db, export_id)
    if row is None:
        return None, "NOT_FOUND"
    now = datetime.now()
    status = str(getattr(row, "publish_status", "") or "").upper()
    expires_at = getattr(row, "publish_lease_expires_at", None)
    if status == "COMPLETED" or getattr(row, "published_model_id", None) is not None:
        return None, "ALREADY_PUBLISHED"
    if status == "PROCESSING" and expires_at is not None and expires_at > now:
        if str(getattr(row, "publish_lease_owner", "") or "") == lease_owner and str(
            getattr(row, "publish_job_id", "") or ""
        ) == str(job_id):
            return row, "CLAIMED"
        return None, "BUSY"
    # Repeat the ownership/expiry predicates in the UPDATE.  The row lock is
    # the MySQL serialization boundary; the conditional UPDATE also protects
    # SQLite and any caller that reaches this function without a surrounding
    # application-level lock.
    updated = (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.id == int(export_id),
            or_(
                YoloBm1688Export.publish_status.is_(None),
                YoloBm1688Export.publish_status != "COMPLETED",
            ),
            YoloBm1688Export.published_model_id.is_(None),
            or_(
                YoloBm1688Export.publish_lease_expires_at.is_(None),
                YoloBm1688Export.publish_lease_expires_at <= now,
            ),
        )
        .update(
            {
                YoloBm1688Export.publish_status: "PROCESSING",
                YoloBm1688Export.publish_job_id: str(job_id)[:64],
                YoloBm1688Export.publish_lease_owner: str(lease_owner)[:128],
                YoloBm1688Export.publish_lease_expires_at: now
                + timedelta(seconds=max(60, int(lease_seconds))),
                YoloBm1688Export.publish_fencing_token: func.coalesce(
                    YoloBm1688Export.publish_fencing_token, 0
                )
                + 1,
            },
            synchronize_session=False,
        )
    )
    if int(updated or 0) != 1:
        return None, "BUSY"
    row = get(db, export_id)
    db.flush()
    db.refresh(row)
    return row, "CLAIMED"


def enqueue_publish(
    db: Session,
    *,
    export_id: int,
    job_id: str,
) -> bool:
    """Persist a durable publication request for the dedicated AICM worker.

    The request is intentionally not leased here.  The worker performs the
    atomic claim so a Web restart cannot leave a claim owned by a dead Web
    process.  A repeated enqueue for the same job is idempotent.
    """
    updated = (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.published_model_id.is_(None),
            or_(
                YoloBm1688Export.publish_status.is_(None),
                YoloBm1688Export.publish_status == "PENDING",
            ),
            or_(
                YoloBm1688Export.publish_job_id.is_(None),
                YoloBm1688Export.publish_job_id == str(job_id),
            ),
        )
        .update(
            {
                YoloBm1688Export.publish_status: "PENDING",
                YoloBm1688Export.publish_job_id: str(job_id)[:64],
                YoloBm1688Export.publish_lease_owner: None,
                YoloBm1688Export.publish_lease_expires_at: None,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return int(updated or 0) == 1


def renew_publish_claim(
    db: Session,
    *,
    export_id: int,
    job_id: str,
    lease_owner: str,
    fencing_token: int,
    lease_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    """Renew only the current publisher's live claim.

    The owner, job id, token and non-expired lease are all part of the
    predicate.  A worker that has been taken over cannot extend its old
    claim, even if it is still running.
    """
    current = now or datetime.now()
    updated = (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.publish_status == "PROCESSING",
            YoloBm1688Export.publish_job_id == str(job_id),
            YoloBm1688Export.publish_lease_owner == str(lease_owner),
            YoloBm1688Export.publish_fencing_token == int(fencing_token),
            YoloBm1688Export.publish_lease_expires_at.isnot(None),
            YoloBm1688Export.publish_lease_expires_at > current,
        )
        .update(
            {
                YoloBm1688Export.publish_lease_expires_at: current
                + timedelta(seconds=max(60, int(lease_seconds)))
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return int(updated or 0) == 1


def finish_publish_claim(
    db: Session,
    *,
    export_id: int,
    job_id: str,
    lease_owner: str,
    fencing_token: int,
    status: str,
    published_model_id: Optional[int] = None,
) -> bool:
    """Finish a publication only if this worker still owns its fencing token."""
    values = {
        YoloBm1688Export.publish_status: str(status).upper()[:32],
        YoloBm1688Export.publish_lease_owner: None,
        YoloBm1688Export.publish_lease_expires_at: None,
    }
    if published_model_id is not None:
        values[YoloBm1688Export.published_model_id] = int(published_model_id)
    updated = (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.publish_job_id == str(job_id),
            YoloBm1688Export.publish_lease_owner == str(lease_owner),
            YoloBm1688Export.publish_fencing_token == int(fencing_token),
            YoloBm1688Export.publish_status == "PROCESSING",
        )
        .update(values, synchronize_session=False)
    )
    db.flush()
    return int(updated) == 1


def get_by_published_model_id(
    db: Session, published_model_id: int
) -> Optional[YoloBm1688Export]:
    return (
        db.query(YoloBm1688Export)
        .filter(YoloBm1688Export.published_model_id == int(published_model_id))
        .order_by(desc(YoloBm1688Export.id))
        .first()
    )


def list_all_for_job(db: Session, *, yolo_train_job_id: int) -> List[YoloBm1688Export]:
    return (
        db.query(YoloBm1688Export)
        .filter(YoloBm1688Export.yolo_train_job_id == yolo_train_job_id)
        .order_by(desc(YoloBm1688Export.id))
        .all()
    )


def delete(db: Session, export_id: int) -> bool:
    n = (
        db.query(YoloBm1688Export)
        .filter(YoloBm1688Export.id == int(export_id))
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n) > 0


def delete_all_for_job(db: Session, *, yolo_train_job_id: int) -> int:
    n = (
        db.query(YoloBm1688Export)
        .filter(YoloBm1688Export.yolo_train_job_id == yolo_train_job_id)
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n)


def list_for_job(
    db: Session, *, yolo_train_job_id: int, limit: int = 5
) -> List[YoloBm1688Export]:
    return (
        db.query(YoloBm1688Export)
        .filter(YoloBm1688Export.yolo_train_job_id == yolo_train_job_id)
        .order_by(desc(YoloBm1688Export.id))
        .limit(max(1, int(limit)))
        .all()
    )


def latest_by_job_ids(
    db: Session, *, yolo_train_job_ids: Iterable[int]
) -> Dict[int, YoloBm1688Export]:
    job_ids = sorted({int(job_id) for job_id in yolo_train_job_ids if job_id})
    if not job_ids:
        return {}
    latest_ids = (
        db.query(func.max(YoloBm1688Export.id).label("id"))
        .filter(YoloBm1688Export.yolo_train_job_id.in_(job_ids))
        .group_by(YoloBm1688Export.yolo_train_job_id)
        .subquery()
    )
    rows = (
        db.query(YoloBm1688Export)
        .join(latest_ids, YoloBm1688Export.id == latest_ids.c.id)
        .all()
    )
    return {int(row.yolo_train_job_id): row for row in rows}


def has_processing_for_job(db: Session, *, yolo_train_job_id: int) -> bool:
    return (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.yolo_train_job_id == yolo_train_job_id,
            YoloBm1688Export.status.in_(("PENDING", "PROCESSING")),
        )
        .first()
        is not None
    )


def list_completed_without_edge_validation_job_ids(db: Session) -> List[int]:
    """Return usable completed exports whose automatic edge job is missing."""
    rows = (
        db.query(YoloBm1688Export.id)
        .join(Task, Task.id == YoloBm1688Export.task_id)
        .outerjoin(
            YoloEdgeValidationJob,
            YoloEdgeValidationJob.export_id == YoloBm1688Export.id,
        )
        .filter(
            YoloBm1688Export.status == "COMPLETED",
            YoloBm1688Export.bmodel_rel_path.isnot(None),
            YoloBm1688Export.bmodel_rel_path != "",
            Task.deleted_at.is_(None),
            YoloEdgeValidationJob.id.is_(None),
        )
        .order_by(YoloBm1688Export.id.asc())
        .all()
    )
    return [int(row[0]) for row in rows]


def has_active_export_for_job(db: Session, *, yolo_train_job_id: int) -> bool:
    """仅当 BM1688 导出正在执行时视为占用；排队中的 PENDING 不阻止删除训练作业。"""
    return (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.yolo_train_job_id == yolo_train_job_id,
            YoloBm1688Export.status == "PROCESSING",
        )
        .first()
        is not None
    )


def _for_user_query(
    db: Session,
    user_id: int,
    *,
    dataset_category_id: Optional[int] = None,
    task_ids: Optional[List[int]] = None,
    task_access_unrestricted: bool = False,
    export_status: Optional[str] = None,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    yolo_train_job_id: Optional[int] = None,
    export_id: Optional[int] = None,
    project_id: Optional[int] = None,
    task_access_builtin_admin: bool = False,
):
    base_filters = [
        Task.deleted_at == None,
        Task.media_type == MediaType.IMAGE.value,
    ]
    base_filters.append(
        task_visibility_clause(
            user_id=user_id,
            unrestricted=task_access_unrestricted,
            builtin_admin=task_access_builtin_admin,
        )
    )
    q = (
        db.query(YoloBm1688Export, Task)
        .join(Task, Task.id == YoloBm1688Export.task_id)
        .filter(*base_filters)
    )
    if export_status and str(export_status).strip():
        q = q.filter(YoloBm1688Export.status == str(export_status).strip())
    if task_name and str(task_name).strip():
        q = q.filter(
            Task.name.ilike(
                literal_like_pattern(str(task_name).strip()), escape=LIKE_ESCAPE
            )
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        q = q.filter(
            or_(
                cast(YoloBm1688Export.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloBm1688Export.yolo_train_job_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                YoloBm1688Export.status.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if yolo_train_job_id is not None:
        q = q.filter(YoloBm1688Export.yolo_train_job_id == int(yolo_train_job_id))
    if export_id is not None:
        q = q.filter(YoloBm1688Export.id == int(export_id))
    if project_id is not None:
        # The export table predates 7GAI project scoping; Task.project_id is
        # therefore the authoritative parent boundary for an export.
        q = q.filter(Task.project_id == int(project_id))
    if dataset_category_id is not None:
        q = q.join(
            YoloTrainJob,
            YoloTrainJob.id == YoloBm1688Export.yolo_train_job_id,
        ).filter(YoloTrainJob.dataset_category_id == int(dataset_category_id))
    if task_ids is not None:
        if not task_ids:
            return None
        q = q.filter(YoloBm1688Export.task_id.in_(task_ids))
    return q


def _with_summary_projection(q):
    # 摘要和系列候选只读取分组、状态与时间字段，避免把编译日志、
    # 制品清单等大字段装入 ORM，降低模型中心统计的 IO 和内存峰值。
    return q.options(
        load_only(
            YoloBm1688Export.id,
            YoloBm1688Export.task_id,
            YoloBm1688Export.yolo_train_job_id,
            YoloBm1688Export.status,
            YoloBm1688Export.quality_report,
            YoloBm1688Export.created_at,
            YoloBm1688Export.updated_at,
        ),
        load_only(Task.id, Task.name, Task.config),
    )


def list_for_user(
    db: Session,
    user_id: int,
    *,
    page: int = 0,
    size: int = 20,
    dataset_category_id: Optional[int] = None,
    task_ids: Optional[List[int]] = None,
    task_access_unrestricted: bool = False,
    export_status: Optional[str] = None,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    yolo_train_job_id: Optional[int] = None,
    export_id: Optional[int] = None,
    project_id: Optional[int] = None,
    task_access_builtin_admin: bool = False,
    summary_projection: bool = False,
    include_total: bool = True,
) -> Tuple[List[Tuple[YoloBm1688Export, Task]], int]:
    q = _for_user_query(
        db,
        user_id,
        dataset_category_id=dataset_category_id,
        task_ids=task_ids,
        task_access_unrestricted=task_access_unrestricted,
        export_status=export_status,
        task_name=task_name,
        search=search,
        yolo_train_job_id=yolo_train_job_id,
        export_id=export_id,
        project_id=project_id,
        task_access_builtin_admin=task_access_builtin_admin,
    )
    if q is None:
        return [], 0
    if summary_projection:
        q = _with_summary_projection(q)
    total = q.count() if include_total else 0
    rows = q.order_by(desc(YoloBm1688Export.id)).offset(page * size).limit(size).all()
    return rows, total


def list_all_for_user(
    db: Session,
    user_id: int,
    *,
    dataset_category_id: Optional[int] = None,
    task_ids: Optional[List[int]] = None,
    task_access_unrestricted: bool = False,
    export_status: Optional[str] = None,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    yolo_train_job_id: Optional[int] = None,
    export_id: Optional[int] = None,
    project_id: Optional[int] = None,
    task_access_builtin_admin: bool = False,
    summary_projection: bool = False,
) -> List[Tuple[YoloBm1688Export, Task]]:
    """Return all matching visible exports for server-side family grouping."""

    q = _for_user_query(
        db,
        user_id,
        dataset_category_id=dataset_category_id,
        task_ids=task_ids,
        task_access_unrestricted=task_access_unrestricted,
        export_status=export_status,
        task_name=task_name,
        search=search,
        yolo_train_job_id=yolo_train_job_id,
        export_id=export_id,
        project_id=project_id,
        task_access_builtin_admin=task_access_builtin_admin,
    )
    if q is None:
        return []
    if summary_projection:
        q = _with_summary_projection(q)
    return q.order_by(desc(YoloBm1688Export.id)).all()


def update_row(db: Session, row: YoloBm1688Export, **kwargs) -> YoloBm1688Export:
    for k, v in kwargs.items():
        if hasattr(row, k):
            setattr(row, k, v)
    db.flush()
    db.refresh(row)
    return row
