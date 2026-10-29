from typing import List

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.algorithm import (
    CreateAlgorithmDefinitionCommand,
    UpdateAlgorithmDefinitionCommand,
)
from labelu.internal.application.response.algorithm import (
    AlgorithmCapabilityResponse,
    AlgorithmDefinitionResponse,
    AlgorithmGroupResponse,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import algorithm as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User


groups_router = APIRouter(prefix="/algorithm-groups", tags=["algorithm-registry"])
router = APIRouter(prefix="/algorithms", tags=["algorithm-registry"])


@router.post("", response_model=OkResp[AlgorithmDefinitionResponse], status_code=201)
async def create_algorithm(
    cmd: CreateAlgorithmDefinitionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AlgorithmDefinitionResponse](
        data=await service.create_definition(db, cmd, current_user)
    )


@groups_router.get("", response_model=OkRespWithMeta[List[AlgorithmGroupResponse]])
async def list_groups(
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按分组名称、标识或描述搜索",
    ),
    status_filter: str | None = Query(default="ACTIVE", alias="status"),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total = await service.list_groups(
        db,
        status_filter=status_filter,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[AlgorithmGroupResponse]](
        meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows
    )


@router.get("", response_model=OkRespWithMeta[List[AlgorithmDefinitionResponse]])
async def list_algorithms(
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按算法名称、标识、任务类型、适配器或分组搜索",
    ),
    status_filter: str | None = Query(default="ACTIVE", alias="status"),
    task_type: str | None = Query(default=None, min_length=1, max_length=64),
    group_key: str | None = Query(default=None, min_length=1, max_length=64),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_definitions(
            db,
            status_filter=status_filter,
            task_type=task_type,
            group_key=group_key,
            search=search,
            page=page,
            size=size,
    )
    return OkRespWithMeta[List[AlgorithmDefinitionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get(
    "/{algorithm_key}/capabilities",
    response_model=OkResp[AlgorithmCapabilityResponse],
)
async def get_capabilities(
    algorithm_key: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AlgorithmCapabilityResponse](
        data=await service.capabilities(db, algorithm_key)
    )


@router.patch("/{algorithm_id}", response_model=OkResp[AlgorithmDefinitionResponse])
async def update_algorithm(
    algorithm_id: int,
    cmd: UpdateAlgorithmDefinitionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AlgorithmDefinitionResponse](
        data=await service.update_definition(db, algorithm_id, cmd, current_user)
    )


# Keep the unconstrained detail route after all subresources. FastAPI matches
# routes in declaration order, so placing this before ``/capabilities`` would
# interpret ``yolo_detection/capabilities`` as an algorithm key and return 404.
@router.get("/{algorithm_key}", response_model=OkResp[AlgorithmDefinitionResponse])
async def get_algorithm(
    algorithm_key: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AlgorithmDefinitionResponse](
        data=await service.get_definition(db, algorithm_key)
    )
