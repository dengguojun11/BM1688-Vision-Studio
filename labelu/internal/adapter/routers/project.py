from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.project import (
    AddProjectMemberCommand,
    CreateProjectCommand,
    UpdateProjectCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.project import (
    ProjectMemberResponse,
    ProjectResponse,
)
from labelu.internal.application.service import project as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User
from labelu.internal.adapter.routers._phase_one_gate import (
    hide_router_from_openapi,
    require_legacy_control_plane_write,
)


router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=OkResp[ProjectResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    cmd: CreateProjectCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[ProjectResponse](
        data=await service.create(db=db, cmd=cmd, current_user=current_user)
    )


@router.get(
    "",
    response_model=OkRespWithMeta[List[ProjectResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_all(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按项目 ID、名称、描述、状态或归属 ID 搜索",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_by(
        db=db, current_user=current_user, page=page, size=size, search=q
    )
    return OkRespWithMeta[List[ProjectResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get(
    "/{project_id}",
    response_model=OkResp[ProjectResponse],
    status_code=status.HTTP_200_OK,
)
async def get(
    project_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[ProjectResponse](
        data=await service.get(db=db, project_id=project_id, current_user=current_user)
    )


@router.patch(
    "/{project_id}",
    response_model=OkResp[ProjectResponse],
    status_code=status.HTTP_200_OK,
)
async def update(
    project_id: int,
    cmd: UpdateProjectCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[ProjectResponse](
        data=await service.update(
            db=db, project_id=project_id, cmd=cmd, current_user=current_user
        )
    )


@router.get(
    "/{project_id}/members",
    response_model=OkResp[List[ProjectMemberResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_members(
    project_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[List[ProjectMemberResponse]](
        data=await service.list_members(
            db=db, project_id=project_id, current_user=current_user
        )
    )


@router.post(
    "/{project_id}/members",
    response_model=OkResp[ProjectMemberResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    project_id: int,
    cmd: AddProjectMemberCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[ProjectMemberResponse](
        data=await service.add_member(
            db=db, project_id=project_id, cmd=cmd, current_user=current_user
        )
    )


hide_router_from_openapi(router)
