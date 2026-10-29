from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.annotation_review import (
    CreateAnnotationReviewCommand,
    TransitionAnnotationReviewCommand,
)
from labelu.internal.application.response.annotation_review import (
    AnnotationReviewResponse,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import annotation_review as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/annotation-reviews", tags=["annotation_review"])


@router.post(
    "",
    response_model=OkResp[AnnotationReviewResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    cmd: CreateAnnotationReviewCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AnnotationReviewResponse](
        data=await service.create(db, cmd, current_user)
    )


@router.get("", response_model=OkRespWithMeta[List[AnnotationReviewResponse]])
async def list_all(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=200),
    review_status: str | None = Query(default=None, alias="status"),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按审核编号、任务、样本、来源、状态或标注摘要搜索",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_by(
        db,
        page=page,
        size=size,
        status_filter=review_status,
        user=current_user,
        search=search,
    )
    return OkRespWithMeta[List[AnnotationReviewResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get("/{review_id}", response_model=OkResp[AnnotationReviewResponse])
async def get(
    review_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AnnotationReviewResponse](
        data=await service.get(db, review_id, current_user)
    )


@router.post("/{review_id}/transition", response_model=OkResp[AnnotationReviewResponse])
async def transition(
    review_id: int,
    cmd: TransitionAnnotationReviewCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AnnotationReviewResponse](
        data=await service.transition(db, review_id, cmd, current_user)
    )
