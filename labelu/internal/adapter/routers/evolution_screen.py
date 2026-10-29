"""Read-only APIs for the model self-evolution cockpit."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import evolution_screen as service
from labelu.internal.common import db as db_module
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.common.task_access import user_has_task_access
from labelu.internal.common.yolo_training_metrics import (
    parse_results_csv_history,
    resolve_train_dir_from_weights,
    train_job_work_train_dir,
)
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/evolution", tags=["evolution_screen"])
metrics_router = APIRouter(tags=["evolution_screen"])


def _not_found(message: str = "进化谱系不存在或无权访问") -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND,
        message=message,
    )


@router.get("/screen/overview", response_model=OkResp[dict])
def screen_overview(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the bounded first-screen projection for the current user."""
    _ = authorization
    return OkResp[dict](data=service.overview(db, current_user))


@router.get("/lineages", response_model=OkRespWithMeta[list[dict]])
def list_lineages(
    q: str | None = Query(None, alias="q", max_length=128),
    keyword: str | None = Query(None, max_length=128),
    status_value: str | None = Query(None, alias="status"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    rows = service.list_lineages(db, current_user)
    effective_keyword = q if q is not None else keyword
    normalized_keyword = str(effective_keyword or "").strip().lower()
    normalized_status = str(status_value or "").strip().upper()
    if normalized_keyword:
        rows = [row for row in rows if normalized_keyword in f"{row['name']} {row['series_type']}".lower()]
    if normalized_status:
        rows = [row for row in rows if row["status"] == normalized_status]
    total = len(rows)
    data = rows[page * size:(page + 1) * size]
    return OkRespWithMeta[list[dict]](
        data=data, meta_data=MetaData(total=total, page=page, size=len(data))
    )


@router.get("/lineages/{lineage_id}", response_model=OkResp[dict])
def get_lineage(
    lineage_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = service.get_lineage(db, current_user, lineage_id)
    if data is None:
        raise _not_found()
    return OkResp[dict](data=data)


@metrics_router.get("/training/jobs/{job_id}/metrics", response_model=OkResp[dict])
def get_incremental_training_metrics(
    job_id: int,
    after_epoch: int | None = Query(None, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Read real Ultralytics epoch rows without repeatedly returning old rows."""
    _ = authorization
    job = crud_yolo_train_job.get(db, job_id)
    task = db.query(Task).filter(Task.id == int(job.task_id)).one_or_none() if job and job.task_id else None
    if job is None or task is None or not user_has_task_access(current_user, task):
        raise _not_found("训练作业不存在或无权访问")
    train_dir = resolve_train_dir_from_weights(getattr(job, "weights_path", None))
    if train_dir is None:
        candidate = train_job_work_train_dir(int(job.id))
        train_dir = candidate if candidate.is_dir() else None
    history = parse_results_csv_history(train_dir) if train_dir is not None else []
    if after_epoch is not None:
        history = [item for item in history if int(item["epoch"]) > after_epoch]
    has_more = len(history) > limit
    history = history[:limit]
    latest = history[-1] if history else None
    return OkResp[dict](data={
        "job_id": int(job.id),
        "available": bool(history),
        "source": "train/results.csv" if train_dir is not None else None,
        "after_epoch": after_epoch,
        "next_after_epoch": latest.get("epoch") if latest else after_epoch,
        "has_more": has_more,
        "history": history,
    })
