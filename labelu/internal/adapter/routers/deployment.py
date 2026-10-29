from typing import List

from datetime import datetime
from fastapi import APIRouter, Depends, Header, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.deployment import (
    ApproveDeploymentReleaseCommand,
    CreateDeploymentObservationCommand,
    CreateDeploymentReleaseCommand,
    DeploymentTransitionCommand,
    ReportDeploymentTargetCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.deployment import (
    DeploymentObservationResponse,
    DeploymentReleaseResponse,
    DeploymentTargetResponse,
    DeploymentTransitionAuditResponse,
)
from labelu.internal.application.service import deployment as service
from labelu.internal.application.service import board_agent
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User
from labelu.internal.adapter.routers._phase_one_gate import (
    ensure_legacy_control_plane_open,
    hide_router_from_openapi,
    require_legacy_control_plane_write,
)

router = APIRouter(prefix="/deployment-releases", tags=["deployment_releases"])


@router.post(
    "",
    response_model=OkResp[DeploymentReleaseResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    cmd: CreateDeploymentReleaseCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[DeploymentReleaseResponse](
        data=await service.create(db, cmd, current_user)
    )


@router.get("", response_model=OkRespWithMeta[List[DeploymentReleaseResponse]])
async def list_all(
    project_id: int = Query(gt=0),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(
        default=None,
        max_length=128,
        description="按发布标识、状态、模型版本或目标设备搜索",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_by(
        db, project_id, page, size, status_filter, current_user, q
    )
    return OkRespWithMeta[List[DeploymentReleaseResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get("/{release_id}", response_model=OkResp[DeploymentReleaseResponse])
async def get(
    release_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[DeploymentReleaseResponse](
        data=await service.get(db, release_id, current_user)
    )


@router.get(
    "/{release_id}/transition-audits",
    response_model=OkRespWithMeta[List[DeploymentTransitionAuditResponse]],
)
async def list_transition_audits(
    release_id: int,
    target_id: int | None = Query(default=None, gt=0),
    actor_type: str | None = Query(default=None, max_length=24),
    actor_id: int | None = Query(default=None, gt=0),
    agent_id: str | None = Query(default=None, max_length=128),
    operation: str | None = Query(default=None, max_length=64),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    rows, total = await service.list_transition_audits(
        db,
        release_id,
        current_user,
        target_id=target_id,
        actor_type=actor_type,
        actor_id=actor_id,
        agent_id=agent_id,
        operation=operation,
        created_after=created_after,
        created_before=created_before,
        offset=page * size,
        limit=size,
    )
    return OkRespWithMeta[List[DeploymentTransitionAuditResponse]](
        meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows
    )


@router.post("/{release_id}/approve", response_model=OkResp[DeploymentReleaseResponse])
async def approve(
    release_id: int,
    cmd: ApproveDeploymentReleaseCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[DeploymentReleaseResponse](
        data=await service.approve(
            db, release_id, cmd or ApproveDeploymentReleaseCommand(), current_user
        )
    )


async def _transition(
    release_id: int,
    operation: str,
    cmd: DeploymentTransitionCommand | None,
    db: Session,
    current_user: User,
) -> OkResp[DeploymentReleaseResponse]:
    return OkResp[DeploymentReleaseResponse](
        data=await service.transition(
            db,
            release_id,
            operation,
            cmd or DeploymentTransitionCommand(),
            current_user,
        )
    )


@router.post("/{release_id}/start", response_model=OkResp[DeploymentReleaseResponse])
async def start(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    """Start an approved release and enter PRECHECKING."""
    return await _transition(release_id, "start", cmd, db, current_user)


@router.post("/{release_id}/precheck", response_model=OkResp[DeploymentReleaseResponse])
async def precheck(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    """Complete controlled prechecks and stage the release."""
    return await _transition(release_id, "precheck", cmd, db, current_user)


@router.post("/{release_id}/canary", response_model=OkResp[DeploymentReleaseResponse])
async def canary(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return await _transition(release_id, "canary", cmd, db, current_user)


@router.post("/{release_id}/promote", response_model=OkResp[DeploymentReleaseResponse])
async def promote(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    """Promote a healthy canary into the observation phase."""
    return await _transition(release_id, "promote", cmd, db, current_user)


@router.post("/{release_id}/expand", response_model=OkResp[DeploymentReleaseResponse])
async def expand(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return await _transition(release_id, "expand", cmd, db, current_user)


@router.post("/{release_id}/complete", response_model=OkResp[DeploymentReleaseResponse])
async def complete(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return await _transition(release_id, "complete", cmd, db, current_user)


@router.post("/{release_id}/rollback", response_model=OkResp[DeploymentReleaseResponse])
async def rollback(
    release_id: int,
    cmd: DeploymentTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return await _transition(release_id, "rollback", cmd, db, current_user)


@router.post(
    "/{release_id}/targets/{device_id}/report",
    response_model=OkResp[DeploymentTargetResponse],
)
async def report_target(
    release_id: int,
    device_id: str,
    cmd: ReportDeploymentTargetCommand,
    _: None = Depends(ensure_legacy_control_plane_open),
    x_board_agent_token: str | None = Header(default=None, alias="X-Board-Agent-Token"),
    db: Session = Depends(db_module.get_db),
):
    board_agent.verify_board_agent_token(x_board_agent_token or "")
    return OkResp[DeploymentTargetResponse](
        data=await service.report_target(db, release_id, device_id, cmd)
    )


@router.post(
    "/{release_id}/targets/{device_id}/observations",
    response_model=OkResp[DeploymentObservationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def observe(
    release_id: int,
    device_id: str,
    cmd: CreateDeploymentObservationCommand,
    _: None = Depends(ensure_legacy_control_plane_open),
    x_board_agent_token: str | None = Header(default=None, alias="X-Board-Agent-Token"),
    db: Session = Depends(db_module.get_db),
):
    board_agent.verify_board_agent_token(x_board_agent_token or "")
    return OkResp[DeploymentObservationResponse](
        data=await service.observe(db, release_id, device_id, cmd)
    )


hide_router_from_openapi(router)
