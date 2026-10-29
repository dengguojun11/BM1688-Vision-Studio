from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.strategy_integration_s3_config import (
    CreateStrategyIntegrationS3ConnectionCommand,
    SetActiveStrategyIntegrationS3ConnectionCommand,
    UpdateStrategyIntegrationS3ConnectionCommand,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.strategy_integration_s3_config import (
    StrategyIntegrationS3ConnectionResponse,
    StrategyIntegrationS3ConnectionTestResponse,
    StrategyIntegrationS3ConnectionsBundleResponse,
)
from labelu.internal.application.service import strategy_integration_s3_config as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/strategy-integration-s3-config", tags=["strategy_integration_s3_config"])


@router.get("", response_model=OkResp[StrategyIntegrationS3ConnectionsBundleResponse])
async def list_connections(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    return OkResp[StrategyIntegrationS3ConnectionsBundleResponse](data=service.list_connections())


@router.post(
    "/connections",
    response_model=OkResp[StrategyIntegrationS3ConnectionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    cmd: CreateStrategyIntegrationS3ConnectionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[StrategyIntegrationS3ConnectionResponse](data=service.create(cmd))


@router.patch(
    "/connections/{connection_id}",
    response_model=OkResp[StrategyIntegrationS3ConnectionResponse],
)
async def update_connection(
    connection_id: str,
    cmd: UpdateStrategyIntegrationS3ConnectionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[StrategyIntegrationS3ConnectionResponse](
        data=service.update(connection_id, cmd)
    )


@router.delete(
    "/connections/{connection_id}",
    response_model=OkResp[StrategyIntegrationS3ConnectionsBundleResponse],
)
async def delete_connection(
    connection_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[StrategyIntegrationS3ConnectionsBundleResponse](
        data=service.delete(connection_id)
    )


@router.post(
    "/connections/{connection_id}/test",
    response_model=OkResp[StrategyIntegrationS3ConnectionTestResponse],
)
async def test_connection(
    connection_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[StrategyIntegrationS3ConnectionTestResponse](
        data=service.test_connection(connection_id)
    )


@router.put(
    "/connections/active",
    response_model=OkResp[StrategyIntegrationS3ConnectionsBundleResponse],
)
async def set_active_connection(
    cmd: SetActiveStrategyIntegrationS3ConnectionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    return OkResp[StrategyIntegrationS3ConnectionsBundleResponse](
        data=service.set_active(cmd)
    )
