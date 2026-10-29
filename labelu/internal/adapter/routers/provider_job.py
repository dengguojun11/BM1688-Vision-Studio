from typing import List

from fastapi import APIRouter, Depends, Header, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.provider_job import (
    CreatePredictionSetCommand,
    CreateProviderJobCommand,
    TransitionPredictionSetReviewCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.provider_job import (
    PredictionSetProjectionResponse,
    PredictionSetReviewAuditResponse,
    PredictionSetResponse,
    ProviderJobResponse,
)
from labelu.internal.application.service import provider_job as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(tags=["provider_jobs"])


@router.post("/provider-jobs", response_model=OkResp[ProviderJobResponse], status_code=status.HTTP_201_CREATED)
async def create_job(cmd: CreateProviderJobCommand, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[ProviderJobResponse](data=await service.create_job(db, cmd, current_user, idempotency_key=idempotency_key))


@router.get("/provider-jobs", response_model=OkRespWithMeta[List[ProviderJobResponse]])
async def list_jobs(
    status_filter: str | None = Query(default=None, alias="status", max_length=24),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按作业编号、Provider、版本、模型、状态或错误信息搜索",
    ),
    legacy_search: str | None = Query(
        default=None,
        alias="search",
        max_length=128,
        include_in_schema=False,
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    normalized_search = str(
        search if search is not None else legacy_search or ""
    ).strip() or None
    rows, total = await service.list_jobs(
        db,
        current_user,
        status_filter=status_filter,
        search=normalized_search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[ProviderJobResponse]](meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows)


@router.get("/provider-jobs/{job_id}", response_model=OkResp[ProviderJobResponse])
async def get_job(job_id: int, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[ProviderJobResponse](data=await service.get_job(db, job_id, current_user))


@router.post("/provider-jobs/{job_id}/cancel", response_model=OkResp[ProviderJobResponse])
async def cancel_job(job_id: int, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[ProviderJobResponse](data=await service.cancel_job(db, job_id, current_user))


@router.post("/provider-jobs/{job_id}/prediction-sets", response_model=OkResp[PredictionSetResponse], status_code=status.HTTP_201_CREATED)
async def create_prediction_set(job_id: int, cmd: CreatePredictionSetCommand, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    if not (idempotency_key or "").strip():
        raise LabelUException(code=ErrorCode.CODE_30002_VALIDATION_ERROR, status_code=422, message="必须提供 Idempotency-Key")
    return OkResp[PredictionSetResponse](
        data=await service.create_prediction_set(
            db, job_id, cmd, current_user, idempotency_key=idempotency_key
        )
    )


@router.get("/prediction-sets", response_model=OkRespWithMeta[List[PredictionSetResponse]])
async def list_prediction_sets(provider_job_id: int | None = Query(default=None, gt=0), sample_id: int | None = Query(default=None, gt=0), review_status: str | None = Query(default=None, alias="review_status", max_length=24), search: str | None = Query(default=None, alias="q", max_length=128, description="按预测集、Provider 作业、样本、Provider 或状态搜索"), page: int = Query(default=0, ge=0), size: int = Query(default=100, ge=1, le=500), authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    rows, total = await service.list_prediction_sets(db, current_user, provider_job_id=provider_job_id, sample_id=sample_id, review_status=review_status, search=search, page=page, size=size)
    return OkRespWithMeta[List[PredictionSetResponse]](meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows)


@router.get("/prediction-sets/{set_id}", response_model=OkResp[PredictionSetResponse])
async def get_prediction_set(set_id: int, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[PredictionSetResponse](data=await service.get_prediction_set(db, set_id, current_user))


@router.post("/prediction-sets/{set_id}/review", response_model=OkResp[PredictionSetResponse])
@router.post("/prediction-sets/{set_id}/review/transition", response_model=OkResp[PredictionSetResponse], include_in_schema=False)
async def review_prediction_set(
    set_id: int,
    cmd: TransitionPredictionSetReviewCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[PredictionSetResponse](data=await service.transition_prediction_review(
        db, set_id, cmd, current_user, idempotency_key=idempotency_key
    ))


@router.post(
    "/prediction-sets/{set_id}/project-legacy",
    response_model=OkResp[PredictionSetProjectionResponse],
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/prediction-sets/{set_id}/legacy-projection",
    response_model=OkResp[PredictionSetProjectionResponse],
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def project_prediction_set_to_legacy(
    set_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[PredictionSetProjectionResponse](data=await service.project_prediction_set_to_legacy(
        db, set_id, current_user, idempotency_key=idempotency_key
    ))


@router.get("/prediction-sets/{set_id}/review-audits", response_model=OkResp[List[PredictionSetReviewAuditResponse]])
async def list_prediction_review_audits(
    set_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[List[PredictionSetReviewAuditResponse]](data=await service.list_prediction_review_audits(db, set_id, current_user))


@router.get("/prediction-sets/{set_id}/projection", response_model=OkResp[PredictionSetProjectionResponse])
async def get_prediction_projection(
    set_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[PredictionSetProjectionResponse](data=await service.get_prediction_projection(db, set_id, current_user))
