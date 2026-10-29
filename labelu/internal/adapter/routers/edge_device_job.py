from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.edge_device_job import (
    ClaimEdgeDeviceJobCommand,
    CreateEdgeDeviceJobCommand,
    SubmitEdgeDeviceJobResultCommand,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.edge_device_job import (
    EdgeDeviceClaimResponse,
    EdgeDeviceJobResponse,
)
from labelu.internal.application.service import edge_device_job as service
from labelu.internal.adapter.routers._phase_one_gate import (
    ensure_legacy_control_plane_open,
    hide_router_from_openapi,
)
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(
    prefix="/edge-device-jobs",
    tags=["边端推理验证作业"],
    dependencies=[Depends(ensure_legacy_control_plane_open)],
)


@router.post(
    "",
    response_model=OkResp[EdgeDeviceJobResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_edge_device_job(
    cmd: CreateEdgeDeviceJobCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data = await service.create(db=db, cmd=cmd, current_user=current_user)
    return OkResp[EdgeDeviceJobResponse](data=data)


@router.post(
    "/claim",
    response_model=OkResp[EdgeDeviceClaimResponse],
    status_code=status.HTTP_200_OK,
)
async def claim_edge_device_job(
    cmd: ClaimEdgeDeviceJobCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data = await service.claim_next(db=db, cmd=cmd)
    return OkResp[EdgeDeviceClaimResponse](data=data)


@router.post(
    "/{job_id}/result",
    response_model=OkResp[EdgeDeviceJobResponse],
    status_code=status.HTTP_200_OK,
)
async def submit_edge_device_job_result(
    job_id: int,
    cmd: SubmitEdgeDeviceJobResultCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data = await service.submit_result(db=db, job_id=job_id, cmd=cmd)
    return OkResp[EdgeDeviceJobResponse](data=data)


hide_router_from_openapi(router)
