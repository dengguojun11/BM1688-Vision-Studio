from typing import Any, List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.workflow import (
    CreateWorkflowCommand,
    TransitionWorkflowCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.workflow import WorkflowResponse, WorkflowStepResponse
from labelu.internal.application.service import workflow as service
from labelu.internal.common import db as db_module
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/workflows", tags=["异常任务与第三方调用"])


@router.post(
    "",
    response_model=OkResp[WorkflowResponse],
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create(
    cmd: CreateWorkflowCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[WorkflowResponse](data=await service.create(db, cmd, current_user))


@router.get(
    "", response_model=OkRespWithMeta[List[WorkflowResponse]], include_in_schema=False
)
async def list_all(
    project_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按工作流编号、类型、状态、幂等键、步骤或错误信息搜索",
    ),
    workflow_type: str | None = Query(
        default=None,
        max_length=48,
        description="按工作流类型精确筛选",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_by(
        db,
        project_id,
        page,
        size,
        status_filter,
        current_user,
        search,
        workflow_type,
    )
    return OkRespWithMeta[List[WorkflowResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get("/outbox", response_model=OkResp[List[dict[str, Any]]])
def list_outbox(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    rows = service.outbox_rows(db, status_filter)
    return OkResp[List[dict[str, Any]]](
        data=[
            {
                "id": r.id,
                "workflow_id": r.workflow_id,
                "event_type": r.event_type,
                "status": r.status,
                "attempts": r.attempts,
                "max_attempts": r.max_attempts,
                "available_at": r.available_at,
                "last_error": r.last_error,
            }
            for r in rows
        ]
    )


@router.post("/outbox/{outbox_id}/retry", response_model=OkResp[dict[str, Any]])
def retry_outbox(
    outbox_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](
        data=service.retry_outbox(db, outbox_id, current_user)
    )


@router.post("/outbox/{outbox_id}/dispatch", response_model=OkResp[dict[str, Any]])
def dispatch_outbox(
    outbox_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    """Run one due third-party callback outbox item through the HTTP adapter."""
    from labelu.internal.application.service import flexible_callback

    service.outbox_for_user(db, outbox_id, current_user)
    return OkResp[dict[str, Any]](
        data=flexible_callback.dispatch_outbox(db, outbox_id=outbox_id)
    )


@router.get(
    "/optimization-jobs/{job_id}",
    response_model=OkResp[dict[str, Any]],
    include_in_schema=False,
)
def get_optimization_job(
    job_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    from labelu.internal.adapter.persistence import crud_model_optimization
    from labelu.internal.application.service.model_optimization import get_job

    job = crud_model_optimization.get(db, job_id)
    if job is None:
        raise service._err("模型优化作业不存在")
    service._project_access(db, int(job.project_id), current_user)
    data = get_job(db, job_id, project_id=int(job.project_id))
    if data is None:
        raise service._err("模型优化作业不存在")
    return OkResp[dict[str, Any]](data=data)


@router.get(
    "/{workflow_id}/steps/{step_key}",
    response_model=OkResp[WorkflowStepResponse],
    include_in_schema=False,
)
async def get_step(
    workflow_id: int,
    step_key: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    """Read one workflow step for lightweight polling and audit views."""
    return OkResp[WorkflowStepResponse](
        data=service.get_step(db, workflow_id, step_key, current_user)
    )


@router.get(
    "/{workflow_id}",
    response_model=OkResp[WorkflowResponse],
    include_in_schema=False,
)
async def get(
    workflow_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[WorkflowResponse](
        data=await service.get(db, workflow_id, current_user)
    )


@router.post(
    "/{workflow_id}/transition",
    response_model=OkResp[WorkflowResponse],
    include_in_schema=False,
)
async def transition(
    workflow_id: int,
    cmd: TransitionWorkflowCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[WorkflowResponse](
        data=await service.transition(db, workflow_id, cmd, current_user)
    )


@router.post(
    "/{workflow_id}/steps/{step_key}/execute",
    response_model=OkResp[dict[str, Any]],
    include_in_schema=False,
)
def execute_step(
    workflow_id: int,
    step_key: str,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Run a registered workflow step through its governed service adapter."""
    with begin_transaction(db):
        data = service.execute_step(db, workflow_id, step_key, current_user)
    return OkResp[dict[str, Any]](data=data)


@router.post(
    "/{workflow_id}/replay",
    response_model=OkResp[WorkflowResponse],
    include_in_schema=False,
)
async def replay(
    workflow_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[WorkflowResponse](
        data=await service.replay(db, workflow_id, current_user)
    )
