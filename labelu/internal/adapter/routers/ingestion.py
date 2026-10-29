from typing import List

from fastapi import APIRouter, Depends, Header, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.ingestion import (
    CreateIngestionJobCommand,
    RegisterMediaAssetCommand,
    RegisterUploadedAssetCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.ingestion import (
    IngestionJobResponse,
    MediaAssetResponse,
)
from labelu.internal.application.service import ingestion as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User


router = APIRouter(tags=["ingestion"])


@router.post(
    "/ingestion-jobs",
    response_model=OkResp[IngestionJobResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_job(
    cmd: CreateIngestionJobCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[IngestionJobResponse](
        data=await service.create_job(
            db, cmd, current_user, idempotency_key=idempotency_key
        )
    )


@router.get(
    "/ingestion-jobs",
    response_model=OkRespWithMeta[List[IngestionJobResponse]],
)
async def list_jobs(
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按接入作业 ID、来源、状态、清单或错误信息搜索",
    ),
    status_filter: str | None = Query(default=None, alias="status", max_length=24),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total = await service.list_jobs(
        db,
        current_user,
        status_filter=status_filter,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[IngestionJobResponse]](
        meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows
    )


@router.get(
    "/ingestion-jobs/{job_id}",
    response_model=OkResp[IngestionJobResponse],
)
async def get_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[IngestionJobResponse](
        data=await service.get_job(db, job_id, current_user)
    )


@router.post(
    "/ingestion-jobs/{job_id}/cancel",
    response_model=OkResp[IngestionJobResponse],
)
async def cancel_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[IngestionJobResponse](
        data=await service.cancel_job(db, job_id, current_user)
    )


@router.post(
    "/ingestion-jobs/{job_id}/assets",
    response_model=OkResp[MediaAssetResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_asset(
    job_id: int,
    cmd: RegisterMediaAssetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaAssetResponse](
        data=await service.register_asset(db, job_id, cmd, current_user)
    )


@router.post(
    "/ingestion-jobs/{job_id}/assets/from-upload-session",
    response_model=OkResp[MediaAssetResponse],
    status_code=status.HTTP_201_CREATED,
)
async def register_uploaded_asset(
    job_id: int,
    cmd: RegisterUploadedAssetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaAssetResponse](
        data=await service.register_uploaded_asset(db, job_id, cmd, current_user)
    )


@router.get(
    "/media-assets",
    response_model=OkRespWithMeta[List[MediaAssetResponse]],
)
async def list_assets(
    job_id: int | None = Query(default=None, gt=0, alias="ingestion_job_id"),
    sha256: str | None = Query(default=None, min_length=64, max_length=64),
    scan_status: str | None = Query(default=None, alias="scan_status", max_length=24),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按文件名、对象路径、类型、摘要或状态搜索媒体资产",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total = await service.list_assets(
        db,
        current_user,
        ingestion_job_id=job_id,
        sha256=sha256,
        scan_status=scan_status,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[MediaAssetResponse]](
        meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows
    )


@router.get(
    "/media-assets/{asset_id}",
    response_model=OkResp[MediaAssetResponse],
)
async def get_asset(
    asset_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaAssetResponse](
        data=await service.get_asset(db, asset_id, current_user)
    )
