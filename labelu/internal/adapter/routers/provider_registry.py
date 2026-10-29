from typing import List

from fastapi import APIRouter, Body, Depends, Query, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.command.provider_registry import UpdateProviderEvidenceCommand
from labelu.internal.application.command.provider_runtime import RunProviderLocalHilCommand
from labelu.internal.application.response.provider_registry import ProviderRegistryResponse
from labelu.internal.application.service import provider_registry as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User


router = APIRouter(prefix="/providers", tags=["providers"])


@router.patch("/{provider_key}/evidence", response_model=OkResp[ProviderRegistryResponse])
async def update_provider_evidence(
    provider_key: str,
    cmd: UpdateProviderEvidenceCommand = Body(...),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ProviderRegistryResponse](
        data=await service.update_evidence(db, provider_key, cmd, current_user)
    )


@router.get("", response_model=OkRespWithMeta[List[ProviderRegistryResponse]])
async def list_providers(
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按 Provider 名称、标识、版本、适配器、能力或状态搜索",
    ),
    status_filter: str | None = Query(default="ACTIVE", alias="status"),
    capability: str | None = Query(default=None, min_length=1, max_length=64),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_providers(
        db,
        current_user=current_user,
        status_filter=status_filter,
        capability=capability,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[ProviderRegistryResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get("/{provider_key}", response_model=OkResp[ProviderRegistryResponse])
async def get_provider(
    provider_key: str,
    provider_version: str | None = Query(default=None, min_length=1, max_length=64),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ProviderRegistryResponse](
        data=await service.get_provider(
            db,
            provider_key,
            current_user=current_user,
            provider_version=provider_version,
        )
    )


@router.post("/{provider_key}/runtime-check", response_model=OkResp[ProviderRegistryResponse])
async def check_provider_runtime(
    provider_key: str,
    provider_version: str | None = Query(default=None, min_length=1, max_length=64),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Run and persist an administrator-only runtime health/readiness probe."""

    from labelu.internal.application.service import provider_runtime as runtime_service

    return OkResp[ProviderRegistryResponse](
        data=runtime_service.runtime_check(
            db,
            provider_key,
            provider_version=provider_version,
            current_user=current_user,
        )
    )


@router.post("/{provider_key}/local-hil", response_model=OkResp[ProviderRegistryResponse])
async def run_provider_local_hil(
    provider_key: str,
    cmd: RunProviderLocalHilCommand = Body(default_factory=RunProviderLocalHilCommand),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute one local HTTP/adapter HIL contract probe without release side effects."""

    from labelu.internal.application.service import provider_runtime as runtime_service

    return OkResp[ProviderRegistryResponse](
        data=runtime_service.local_hil_check(
            db, provider_key, command=cmd, current_user=current_user
        )
    )
