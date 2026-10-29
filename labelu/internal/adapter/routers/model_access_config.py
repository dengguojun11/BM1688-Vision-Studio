from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from labelu.internal.application.command.model_access_config import (
    CreateModelAccessCommand,
    UpdateModelAccessCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.model_access_config import (
    ModelAccessConfigBundleResponse,
    ModelAccessConfigResponse,
    ModelAccessConfigTestResponse,
)
from labelu.internal.application.service import model_access_config as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

# 使用业务中文标签，避免 OpenAPI 将此路由归为“接口：...”通用分组。
# 管理后台和文档展示都应直接告诉用户这是模型接入配置。
router = APIRouter(prefix="/ai-model-configs", tags=["模型接入配置"])


@router.get("", response_model=OkRespWithMeta[ModelAccessConfigBundleResponse])
async def list_configs(
    q: str | None = Query(
        default=None,
        max_length=128,
        description="按配置名称、模型、服务地址、提供商、能力或状态搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    data, total = service.list_configs_page(q=q, page=page, size=size)
    return OkRespWithMeta[ModelAccessConfigBundleResponse](
        data=data,
        meta_data=MetaData(total=total, page=page, size=len(data.configs)),
    )


@router.post("", response_model=OkResp[ModelAccessConfigResponse], status_code=status.HTTP_201_CREATED)
async def create_config(
    cmd: CreateModelAccessCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[ModelAccessConfigResponse](data=service.create(cmd))


@router.patch("/{config_id}", response_model=OkResp[ModelAccessConfigResponse])
async def update_config(
    config_id: str,
    cmd: UpdateModelAccessCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[ModelAccessConfigResponse](data=service.update(config_id, cmd))


@router.delete("/{config_id}", response_model=OkResp[dict])
async def delete_config(
    config_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    service.delete(config_id)
    return OkResp[dict](data={"deleted": True, "id": config_id})


@router.post("/{config_id}/test", response_model=OkResp[ModelAccessConfigTestResponse])
async def test_config(
    config_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    # The connectivity probe uses a synchronous HTTP client.  Run it outside
    # the event loop so a local health endpoint (or any service sharing this
    # process) can answer while the probe is in progress.
    result = await run_in_threadpool(service.test_connection, config_id)
    return OkResp[ModelAccessConfigTestResponse](data=result)
