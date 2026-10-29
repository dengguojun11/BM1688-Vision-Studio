from typing import Dict, List, Optional, Tuple

from sqlalchemy import exists, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def delete_all_for_job(db: Session, *, job_id: int) -> int:
    n = (
        db.query(YoloEvalReport)
        .filter(YoloEvalReport.yolo_train_job_id == job_id)
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n)


def delete_by_job_and_kind(db: Session, *, job_id: int, report_kind: str) -> int:
    q = db.query(YoloEvalReport).filter(
        YoloEvalReport.yolo_train_job_id == job_id,
        YoloEvalReport.report_kind == report_kind,
    )
    n = q.delete(synchronize_session=False)
    db.flush()
    return int(n)


def get_by_id(db: Session, report_id: int) -> Optional[YoloEvalReport]:
    return db.query(YoloEvalReport).filter(YoloEvalReport.id == report_id).first()


def get_by_operation(
    db: Session,
    *,
    job_id: int,
    report_kind: str,
    provider_key: str,
    operation_id: str,
) -> Optional[YoloEvalReport]:
    """Return the report for one evaluation generation, if it already exists."""
    if not str(operation_id or "").strip():
        return None
    return (
        db.query(YoloEvalReport)
        .filter(
            YoloEvalReport.yolo_train_job_id == int(job_id),
            YoloEvalReport.report_kind == str(report_kind).strip(),
            YoloEvalReport.provider_key == str(provider_key).strip(),
            YoloEvalReport.operation_id == str(operation_id).strip(),
        )
        .first()
    )


def list_for_user(
    db: Session,
    user_id: int,
    *,
    report_kind: str,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    task_ids: Optional[List[int]] = None,
    page: int = 0,
    size: int = 20,
    task_access_unrestricted: bool = False,
    project_id: Optional[int] = None,
) -> Tuple[List[Tuple[YoloEvalReport, YoloTrainJob, Task]], int]:
    base_filters = [
        Task.deleted_at == None,
        Task.media_type == MediaType.IMAGE.value,
        YoloEvalReport.report_kind == report_kind,
    ]
    if not task_access_unrestricted:
        collaborator_exists = exists().where(
            TaskCollaborator.task_id == Task.id,
            TaskCollaborator.user_id == user_id,
        )
        base_filters.append(or_(Task.created_by == user_id, collaborator_exists))
    q = (
        db.query(YoloEvalReport, YoloTrainJob, Task)
        .join(YoloTrainJob, YoloTrainJob.id == YoloEvalReport.yolo_train_job_id)
        .join(Task, Task.id == YoloTrainJob.task_id)
        .filter(*base_filters)
    )
    if task_name and task_name.strip():
        q = q.filter(
            Task.name.ilike(literal_like_pattern(task_name), escape=LIKE_ESCAPE)
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        q = q.filter(
            or_(
                Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                YoloEvalReport.provider_key.ilike(pattern, escape=LIKE_ESCAPE),
                YoloEvalReport.report_kind.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if task_ids is not None:
        if not task_ids:
            return [], 0
        q = q.filter(YoloTrainJob.task_id.in_(task_ids))
    if project_id is not None:
        q = q.filter(Task.project_id == int(project_id))

    total = q.count()
    rows = (
        q.order_by(YoloEvalReport.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def create(
    db: Session,
    *,
    job_id: int,
    provider_key: str,
    report_kind: str,
    body: str | None,
    suggested_params: str | None,
    error_message: str | None,
    operation_id: str | None = None,
) -> YoloEvalReport | None:
    # The application normally performs the current-generation check before
    # calling this function.  Looking up an existing row here also makes
    # provider retries idempotent when the same operation is replayed.
    normalized_operation = str(operation_id or "").strip() or None
    if normalized_operation:
        # Re-check ownership in the same transaction as the insert. The
        # pre-call check in the evaluator closes the common case, while this
        # row lock prevents a stale provider from inserting after a recovery
        # process has issued a newer operation id.
        operation_field = (
            "ai_eval_full_operation_id"
            if str(report_kind).strip() == "full_analysis"
            else "ai_eval_next_operation_id"
        )
        current_job = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.id == int(job_id))
            .with_for_update()
            .first()
        )
        if not current_job or getattr(current_job, operation_field, None) != normalized_operation:
            return None
        existing = get_by_operation(
            db,
            job_id=job_id,
            report_kind=report_kind,
            provider_key=provider_key,
            operation_id=normalized_operation,
        )
        if existing is not None:
            return existing
    row = YoloEvalReport(
        yolo_train_job_id=job_id,
        provider_key=provider_key,
        report_kind=report_kind,
        operation_id=normalized_operation,
        body=body,
        suggested_params=suggested_params,
        error_message=error_message,
    )
    if normalized_operation:
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # A concurrent retry may have won the unique key race. Return the
            # committed row so callers remain idempotent.
            existing = get_by_operation(
                db,
                job_id=job_id,
                report_kind=report_kind,
                provider_key=provider_key,
                operation_id=normalized_operation,
            )
            if existing is not None:
                return existing
            raise
    else:
        db.add(row)
        db.flush()
    db.refresh(row)
    return row


def list_by_job_and_kind(
    db: Session,
    *,
    job_id: int,
    report_kind: str | None = None,
    operation_id: str | None = None,
) -> list[YoloEvalReport]:
    q = db.query(YoloEvalReport).filter(YoloEvalReport.yolo_train_job_id == job_id)
    if report_kind:
        q = q.filter(YoloEvalReport.report_kind == report_kind)
    if operation_id:
        q = q.filter(YoloEvalReport.operation_id == str(operation_id).strip())
    return q.order_by(YoloEvalReport.id.asc()).all()


def latest_full_analysis_per_provider_for_jobs(
    db: Session, *, job_ids: List[int]
) -> Dict[int, Dict[str, YoloEvalReport]]:
    """每个作业、每个 provider 取 id 最大的一条 full_analysis（用于模型列表展示）。"""
    if not job_ids:
        return {}
    rows = (
        db.query(YoloEvalReport)
        .filter(
            YoloEvalReport.yolo_train_job_id.in_(job_ids),
            YoloEvalReport.report_kind == "full_analysis",
        )
        .order_by(YoloEvalReport.id.desc())
        .all()
    )
    out: Dict[int, Dict[str, YoloEvalReport]] = {}
    for r in rows:
        jid = int(r.yolo_train_job_id)
        pk = (r.provider_key or "").strip()
        if not pk:
            continue
        bucket = out.setdefault(jid, {})
        if pk not in bucket:
            bucket[pk] = r
    return out


def latest_full_bodies_for_prior(db: Session, *, job_id: int, limit: int = 6) -> list[str]:
    rows = (
        db.query(YoloEvalReport)
        .filter(
            YoloEvalReport.yolo_train_job_id == job_id,
            YoloEvalReport.report_kind == "full_analysis",
            YoloEvalReport.body.isnot(None),
            YoloEvalReport.error_message.is_(None),
        )
        .order_by(YoloEvalReport.id.desc())
        .limit(limit)
        .all()
    )
    out: list[str] = []
    for r in rows:
        if r.body:
            out.append(r.body)
    return out
