from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.model_series import (
    CreateModelSeriesCommand,
    ResolveModelLineageCommand,
    UpdateModelSeriesCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.model_series import (
    ModelLineageResolveResponse,
    ModelSeriesResponse,
)
from labelu.internal.application.service import model_series as resolver
from labelu.internal.application.service import model_series_catalog as catalog
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/model-series", tags=["model-series"])


@router.post("", response_model=OkResp[ModelSeriesResponse], status_code=status.HTTP_201_CREATED)
async def create_series(
    cmd: CreateModelSeriesCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[ModelSeriesResponse](data=catalog.create(db, user=current_user, cmd=cmd))


@router.get("", response_model=OkRespWithMeta[List[ModelSeriesResponse]])
async def list_series(
    search: str | None = Query(default=None, alias="q", max_length=128),
    status_value: str | None = Query(default="ACTIVE", alias="status"),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    rows, total = catalog.list_series(
        db, user=current_user, page=page, size=size, search=search, status_value=status_value
    )
    return OkRespWithMeta[List[ModelSeriesResponse]](
        meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows
    )


@router.post("/resolve", response_model=OkResp[ModelLineageResolveResponse])
async def resolve_lineage(
    cmd: ResolveModelLineageCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    lineage = resolver.resolve_model_lineage(
        db,
        current_user=current_user,
        selection_contract=cmd.selection_contract,
        branch_key=cmd.branch_key,
        series_id=cmd.series_id,
        variant_id=cmd.variant_id,
        parent_model_version_id=cmd.parent_model_version_id,
        parent_training_job_id=cmd.parent_training_job_id,
        inheritance_mode=cmd.inheritance_mode,
        series_name=cmd.series_name,
        variant_name=cmd.variant_name,
        model_family=cmd.model_family,
        create_if_missing=cmd.create_if_missing,
    )
    # The caller of this endpoint explicitly requested a resolution. Commit
    # newly materialized series/variants while training routes retain their
    # surrounding transaction semantics.
    db.commit()
    return OkResp[ModelLineageResolveResponse](
        data=ModelLineageResolveResponse(**resolver.serialize_lineage(lineage))
    )


@router.get("/{series_id}", response_model=OkResp[ModelSeriesResponse])
async def get_series(
    series_id: int,
    include_variants: bool = Query(default=True),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[ModelSeriesResponse](
        data=catalog.get(db, user=current_user, series_id=series_id, include_variants=include_variants)
    )


@router.patch("/{series_id}", response_model=OkResp[ModelSeriesResponse])
async def update_series(
    series_id: int,
    cmd: UpdateModelSeriesCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[ModelSeriesResponse](
        data=catalog.update(db, user=current_user, series_id=series_id, cmd=cmd)
    )

