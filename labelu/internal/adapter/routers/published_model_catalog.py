from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Security, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from labelu.internal.application.command.published_model_catalog import (
    CreatePublishedModelAliasCommand,
    SyncPublishedModelsCommand,
    UpdatePublishedModelCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.published_model_catalog import (
    PublishedModelDownloadResponse,
    PublishedModelListResponse,
    PublishedModelResponse,
    PublishedModelSyncResponse,
)
from labelu.internal.application.service import published_model_catalog as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/published-models", tags=["published_models"])
public_router = APIRouter(prefix="/public/models", tags=["published_models_public"])
public_v2_router = APIRouter(
    prefix="/public/models", tags=["published_models_public_v2"]
)


@router.get("", response_model=OkRespWithMeta[PublishedModelListResponse])
async def list_published_models(
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=256),
    export_id: Optional[int] = Query(None, ge=1),
    yolo_train_job_id: Optional[int] = Query(None, ge=1),
    model_status: str = Query("", alias="status", max_length=32),
    availability_status: str = Query("", max_length=32),
    source_type: str = Query("", max_length=32),
    is_public: Optional[bool] = Query(None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, current_user
    data, total = service.list_models(
        db,
        page=page,
        size=size,
        q=q,
        export_id=export_id,
        yolo_train_job_id=yolo_train_job_id,
        status=model_status,
        availability_status=availability_status,
        source_type=source_type,
        is_public=is_public,
    )
    return OkRespWithMeta[PublishedModelListResponse](
        meta_data=MetaData(total=total, page=page, size=len(data.models)), data=data
    )


@router.post(
    "/sync",
    response_model=OkResp[PublishedModelSyncResponse],
    status_code=status.HTTP_200_OK,
)
def sync_published_models(
    cmd: SyncPublishedModelsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[PublishedModelSyncResponse](
        data=service.sync_models(
            db,
            environment_id=cmd.environment_id or "",
            include_remote_models=cmd.include_remote_models,
        )
    )


@router.get("/{identifier}", response_model=OkResp[PublishedModelResponse])
async def get_published_model(
    identifier: str,
    source_system: Optional[str] = Query(None, max_length=64),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, current_user
    return OkResp[PublishedModelResponse](
        data=service.get_model(db, identifier, source_system=source_system)
    )


@router.patch("/{catalog_id}", response_model=OkResp[PublishedModelResponse])
async def update_published_model(
    catalog_id: int,
    cmd: UpdatePublishedModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    return OkResp[PublishedModelResponse](
        data=service.update_model(db, current_user, catalog_id, cmd)
    )


@router.post(
    "/{catalog_id}/aliases",
    response_model=OkResp[PublishedModelResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_published_model_alias(
    catalog_id: int,
    cmd: CreatePublishedModelAliasCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    return OkResp[PublishedModelResponse](
        data=service.add_alias(db, current_user, catalog_id, cmd)
    )


@router.delete(
    "/{catalog_id}/aliases/{alias_id}",
    response_model=OkResp[PublishedModelResponse],
)
async def delete_published_model_alias(
    catalog_id: int,
    alias_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[PublishedModelResponse](
        data=service.remove_alias(db, catalog_id, alias_id)
    )


@router.get("/{identifier}/download", response_class=FileResponse)
async def download_published_model(
    identifier: str,
    source_system: Optional[str] = Query(None, max_length=64),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, current_user
    resolved = service.resolve_download_file(
        db, identifier, source_system=source_system, public_only=False
    )
    return FileResponse(
        path=str(resolved.file_path),
        filename=resolved.filename,
        media_type="application/zip",
    )


@router.get(
    "/{identifier}/download-url",
    response_model=OkResp[PublishedModelDownloadResponse],
)
async def get_published_model_download_url(
    identifier: str,
    source_system: Optional[str] = Query(None, max_length=64),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, current_user
    return OkResp[PublishedModelDownloadResponse](
        data=service.resolve_download(
            db, identifier, source_system=source_system, public_only=False
        )
    )


@public_router.get("", response_model=OkRespWithMeta[PublishedModelListResponse])
async def list_public_models(
    request: Request,
    page: int = Query(0, ge=0),
    size: Optional[int] = Query(None, ge=1, le=500),
    q: str = Query("", max_length=256),
    name: str = Query("", max_length=256),
    event: str = Query("", max_length=256),
    f1: Optional[float] = Query(None, ge=0, le=100),
    db: Session = Depends(db_module.get_db),
):
    query_params = request.query_params
    if size is None:
        for alias in ("page_size", "pageSize", "limit"):
            raw_size = query_params.get(alias)
            if raw_size not in (None, ""):
                try:
                    size = int(raw_size)
                except (TypeError, ValueError):
                    size = None
                break
    if not name:
        name = (
            query_params.get("model_name")
            or query_params.get("modelName")
            or ""
        )
    if not event:
        event = (
            query_params.get("event_name")
            or query_params.get("eventName")
            or query_params.get("label")
            or ""
        )
    if f1 is None:
        for alias in ("f1_value", "f1_score", "f1Score"):
            raw_f1 = query_params.get(alias)
            if raw_f1 not in (None, ""):
                try:
                    f1 = float(raw_f1)
                except (TypeError, ValueError):
                    f1 = None
                break
    if page == 0:
        for alias in ("page_no", "pageNum"):
            raw_page = query_params.get(alias)
            if raw_page not in (None, ""):
                try:
                    page = max(0, int(raw_page))
                except (TypeError, ValueError):
                    pass
                break
    data, total, page_size = service.list_public_models(
        db,
        page=page,
        size=size if size is not None else (20 if page > 0 else None),
        q=q,
        name=name,
        event=event,
        f1=f1,
        base_url=str(request.base_url),
    )
    return OkRespWithMeta[PublishedModelListResponse](
        meta_data=MetaData(
            total=total,
            page=page if (size is not None or page > 0) else 0,
            size=page_size,
        ),
        data=data,
    )


@public_router.get("/{identifier}", response_model=OkResp[PublishedModelResponse])
async def get_public_model(
    request: Request,
    identifier: str,
    source_system: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(db_module.get_db),
):
    return OkResp[PublishedModelResponse](
        data=service.get_model(
            db,
            identifier,
            source_system=source_system,
            public_only=True,
            base_url=str(request.base_url),
        )
    )


@public_router.get("/{identifier}/download", response_class=FileResponse)
async def download_public_model(
    identifier: str,
    source_system: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(db_module.get_db),
):
    resolved = service.resolve_download_file(
        db, identifier, source_system=source_system, public_only=True
    )
    return FileResponse(
        path=str(resolved.file_path),
        filename=resolved.filename,
        media_type="application/zip",
    )


@public_v2_router.get("/{identifier}/download", response_class=FileResponse)
async def download_public_model_v2(
    identifier: str,
    source_system: Optional[str] = Query(None, max_length=64),
    db: Session = Depends(db_module.get_db),
):
    """Download the V2 layout at /api/v2 without changing the V1 package."""
    file_path, filename = service.build_v2_download_file(
        db, identifier, source_system=source_system, public_only=True
    )
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(file_path.unlink, missing_ok=True),
    )
