from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.organization import CreateOrganizationCommand, CreateSceneCommand
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.organization import OrganizationResponse, SceneResponse
from labelu.internal.application.service import organization as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User
from labelu.internal.adapter.routers._phase_one_gate import (
    hide_router_from_openapi,
    require_legacy_control_plane_write,
)


router = APIRouter(prefix="/organizations", tags=["organizations"])
scene_router = APIRouter(prefix="/projects", tags=["scenes"])


@router.post("", response_model=OkResp[OrganizationResponse], status_code=status.HTTP_201_CREATED)
async def create(
    cmd: CreateOrganizationCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    _ = authorization
    return OkResp[OrganizationResponse](data=service.create_organization(db, cmd, current_user))


@router.get("", response_model=OkRespWithMeta[List[OrganizationResponse]])
async def list_all(
    page: int = Query(default=0, ge=0), size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db), current_user: User = Depends(require_system_admin),
):
    _ = authorization
    rows, total = service.list_organizations(db, current_user, page, size)
    return OkRespWithMeta[List[OrganizationResponse]](meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows)


@scene_router.post("/{project_id}/scenes", response_model=OkResp[SceneResponse], status_code=status.HTTP_201_CREATED)
async def create_scene(
    project_id: int, cmd: CreateSceneCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db), current_user: User = Depends(require_legacy_control_plane_write),
):
    _ = authorization
    return OkResp[SceneResponse](data=service.create_scene(db, project_id, cmd, current_user))


@scene_router.get("/{project_id}/scenes", response_model=OkRespWithMeta[List[SceneResponse]])
async def list_scenes(
    project_id: int, page: int = Query(default=0, ge=0), size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db), current_user: User = Depends(require_system_admin),
):
    _ = authorization
    rows, total = service.list_scenes(db, project_id, current_user, page, size)
    return OkRespWithMeta[List[SceneResponse]](meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows)


@scene_router.get("/{project_id}/scenes/{scene_id}", response_model=OkResp[SceneResponse])
async def get_scene(
    project_id: int, scene_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db), current_user: User = Depends(require_system_admin),
):
    _ = authorization
    return OkResp[SceneResponse](data=service.get_scene(db, project_id, scene_id, current_user))


hide_router_from_openapi(router)
hide_router_from_openapi(scene_router)
