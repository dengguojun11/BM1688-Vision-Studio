"""Fast, permission-scoped aggregates for the 7GAI workbench."""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import String, cast, func, literal, or_, union_all
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.workspace import WorkspaceSummaryResponse
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.published_model_catalog import PublishedModelCatalog
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.domain.models.task import Task, TaskStatus
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


router = APIRouter(prefix="/workspace", tags=["工作台"])


def _visible_task_ids(db: Session, user: User):
    """Return a SQL query so every aggregate reuses the task permission rule."""
    return crud_task.visible_query(
        db,
        owner_id=user.id,
        list_all_tasks=is_task_superuser(user),
    ).with_entities(Task.id)


def _catalog_query(db: Session, user: User, visible_task_ids):
    query = db.query(PublishedModelCatalog).filter(
        PublishedModelCatalog.deleted_at.is_(None),
        PublishedModelCatalog.is_public.is_(True),
    )
    if is_task_superuser(user):
        return query
    return query.filter(
        or_(
            PublishedModelCatalog.task_id.is_(None),
            PublishedModelCatalog.task_id.in_(visible_task_ids),
        )
    )


@router.get("/summary", response_model=OkResp[WorkspaceSummaryResponse])
def summary(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
) -> OkResp[WorkspaceSummaryResponse]:
    """Return one bounded aggregate instead of issuing many page-level calls."""
    visible_tasks = crud_task.visible_query(
        db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    )
    visible_task_ids = visible_tasks.with_entities(Task.id)
    task_count = visible_tasks.count()
    pending_annotation = visible_tasks.filter(
        Task.status != TaskStatus.FINISHED.value
    ).count()
    model_count = db.query(func.count(YoloTrainJob.id)).filter(
        YoloTrainJob.task_id.in_(visible_task_ids),
        YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
    ).scalar() or 0
    catalog = _catalog_query(db, current_user, visible_task_ids)
    published_models = catalog.count()
    disabled_models = catalog.filter(
        PublishedModelCatalog.download_enabled.is_(False)
    ).count()
    active_training = db.query(func.count(YoloTrainJob.id)).filter(
        YoloTrainJob.task_id.in_(visible_task_ids),
        YoloTrainJob.status.in_(
            (YoloTrainStatus.PENDING.value, YoloTrainStatus.PROCESSING.value)
        ),
    ).scalar() or 0
    failed_training = db.query(func.count(YoloTrainJob.id)).filter(
        YoloTrainJob.task_id.in_(visible_task_ids),
        YoloTrainJob.status == YoloTrainStatus.FAILED.value,
    ).scalar() or 0
    feedback_scope = literal(True)
    if not is_task_superuser(current_user):
        feedback_scope = or_(
            StrategyTaskIntegration.labelu_task_id.in_(visible_task_ids),
            StrategyTaskIntegration.created_by == current_user.id,
        )
    pending_feedback = db.query(func.count(StrategyTaskIntegration.id)).filter(
        StrategyTaskIntegration.deleted_at.is_(None),
        StrategyTaskIntegration.import_status.in_(("PENDING", "PROCESSING")),
        feedback_scope,
    ).scalar() or 0

    return OkResp[WorkspaceSummaryResponse](
        data=WorkspaceSummaryResponse(
            task_count=int(task_count),
            pending_annotation_task_count=int(pending_annotation),
            model_count=int(model_count),
            published_model_count=int(published_models),
            download_disabled_model_count=int(disabled_models),
            active_training_count=int(active_training),
            failed_training_count=int(failed_training),
            pending_feedback_count=int(pending_feedback),
            generated_at=datetime.now(),
        )
    )


def _activity_query(
    db: Session,
    visible_task_ids,
    *,
    failed_only: bool,
    search: str | None = None,
):
    training = db.query(
        YoloTrainJob.id.label("resource_id"),
        literal("TRAINING").label("job_type"),
        YoloTrainJob.task_id.label("task_id"),
        YoloTrainJob.status.label("status"),
        YoloTrainJob.error_message.label("message"),
        YoloTrainJob.created_at.label("created_at"),
        YoloTrainJob.updated_at.label("updated_at"),
    ).join(Task, Task.id == YoloTrainJob.task_id).filter(
        YoloTrainJob.task_id.in_(visible_task_ids)
    )
    exports = db.query(
        YoloBm1688Export.id.label("resource_id"),
        literal("BM1688_COMPILE").label("job_type"),
        YoloBm1688Export.task_id.label("task_id"),
        YoloBm1688Export.status.label("status"),
        YoloBm1688Export.error_message.label("message"),
        YoloBm1688Export.created_at.label("created_at"),
        YoloBm1688Export.updated_at.label("updated_at"),
    ).join(Task, Task.id == YoloBm1688Export.task_id).filter(
        YoloBm1688Export.task_id.in_(visible_task_ids)
    )
    validations = db.query(
        YoloEdgeValidationJob.id.label("resource_id"),
        literal("EDGE_VALIDATION").label("job_type"),
        YoloEdgeValidationJob.task_id.label("task_id"),
        YoloEdgeValidationJob.status.label("status"),
        YoloEdgeValidationJob.error_message.label("message"),
        YoloEdgeValidationJob.created_at.label("created_at"),
        YoloEdgeValidationJob.updated_at.label("updated_at"),
    ).join(Task, Task.id == YoloEdgeValidationJob.task_id).filter(
        YoloEdgeValidationJob.task_id.in_(visible_task_ids)
    )
    if failed_only:
        training = training.filter(YoloTrainJob.status == "FAILED")
        exports = exports.filter(YoloBm1688Export.status == "FAILED")
        validations = validations.filter(YoloEdgeValidationJob.status == "FAILED")
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        for query, model in (
            (training, YoloTrainJob),
            (exports, YoloBm1688Export),
            (validations, YoloEdgeValidationJob),
        ):
            query = query.filter(
                or_(
                    cast(model.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                    cast(model.task_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                    Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.description.ilike(pattern, escape=LIKE_ESCAPE),
                    model.status.ilike(pattern, escape=LIKE_ESCAPE),
                    model.error_message.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
            if model is YoloTrainJob:
                training = query
            elif model is YoloBm1688Export:
                exports = query
            else:
                validations = query
    return union_all(training.statement, exports.statement, validations.statement).alias(
        "workspace_activity"
    )


def _activity_page(db: Session, activity, page: int, size: int):
    total = db.query(func.count()).select_from(activity).scalar() or 0
    rows = (
        db.query(activity)
        .order_by(
            func.coalesce(activity.c.updated_at, activity.c.created_at).desc(),
            activity.c.job_type.asc(),
            activity.c.resource_id.desc(),
        )
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, int(total)


@router.get("/alerts", response_model=OkRespWithMeta[List[dict]])
def alerts(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, alias="q", max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
) -> OkRespWithMeta[List[dict]]:
    activity = _activity_query(
        db, _visible_task_ids(db, current_user), failed_only=True, search=search
    )
    rows, total = _activity_page(db, activity, page, size)
    default_messages = {
        "TRAINING": "模型训练失败",
        "BM1688_COMPILE": "BM1688 模型编译失败",
        "EDGE_VALIDATION": "BM1688 真机验证失败",
    }
    data = [
        {
            "id": f"{row.job_type.lower()}:{row.resource_id}",
            "resource_id": row.resource_id,
            "task_id": row.task_id,
            "type": row.job_type,
            "severity": "ERROR",
            "status": row.status,
            "message": row.message or default_messages[row.job_type],
            "created_at": row.updated_at or row.created_at,
        }
        for row in rows
    ]
    return OkRespWithMeta[List[dict]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get("/recent-jobs", response_model=OkRespWithMeta[List[dict]])
def recent_jobs(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, alias="q", max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
) -> OkRespWithMeta[List[dict]]:
    activity = _activity_query(
        db, _visible_task_ids(db, current_user), failed_only=False, search=search
    )
    rows, total = _activity_page(db, activity, page, size)
    data = [
        {
            "id": f"{row.job_type.lower()}:{row.resource_id}",
            "resource_id": row.resource_id,
            "task_id": row.task_id,
            "job_type": row.job_type,
            "status": row.status,
            "message": row.message,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]
    return OkRespWithMeta[List[dict]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )
