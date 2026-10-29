"""事件审核模块 - HTTP 路由"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status as http_status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.event_review import (
    EventReviewActionCommand,
    EventReviewClaimCommand,
    EventReviewListCommand,
    EventReviewSetTokenCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.event_review import (
    EventReviewDetail,
    EventReviewItem,
    EventReviewProgress,
)
from labelu.internal.application.service import event_review as event_review_service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/event-review", tags=["event_review"])


@router.post(
    "/config",
    status_code=http_status.HTTP_200_OK,
)
async def set_token(
    cmd: EventReviewSetTokenCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """设置老平台 token（兼容旧接口；生产建议只使用 .env 配置）"""
    _ = authorization, db, current_user
    event_review_service.set_runtime_token(cmd.token)
    return {"ok": True}


@router.get(
    "/images/proxy",
    status_code=http_status.HTTP_200_OK,
)
async def proxy_image(
    path: str = Query(..., description="老平台图片路径，如 /permanent/2026/06/07/xxx.jpg"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """代理老平台图片（绕过 CORS / 证书问题）"""
    _ = authorization, db, current_user
    if not path or ".." in path:
        raise HTTPException(status_code=400, detail="无效图片路径")
    try:
        image_bytes, content_type = event_review_service.get_image_bytes(path)
    except event_review_service.EventReviewConfigError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if not image_bytes:
        raise HTTPException(status_code=404, detail="图片不可用")
    media_type = (content_type or "image/jpeg").split(";", 1)[0]
    return Response(content=image_bytes, media_type=media_type)


@router.get(
    "",
    response_model=OkRespWithMeta[List[EventReviewItem]],
    status_code=http_status.HTTP_200_OK,
)
async def list_events(
    page: int = Query(0, ge=0),
    size: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None, alias="q", max_length=128),
    keyword: Optional[str] = Query(None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    effective_keyword = q if q is not None else keyword
    try:
        data, total = event_review_service.list_events(
            page=page, size=size, status=status, keyword=effective_keyword,
        )
    except event_review_service.EventReviewConfigError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return OkRespWithMeta[List[EventReviewItem]](
        data=data,
        meta_data=MetaData(total=total, page=page, size=len(data)),
    )


@router.get(
    "/progress",
    response_model=OkResp[EventReviewProgress],
    status_code=http_status.HTTP_200_OK,
)
async def get_progress(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    try:
        data = event_review_service.get_progress()
    except event_review_service.EventReviewConfigError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return OkResp[EventReviewProgress](data=data)


@router.get(
    "/{event_id}",
    response_model=OkResp[EventReviewDetail],
    status_code=http_status.HTTP_200_OK,
)
async def get_event_detail(
    event_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    try:
        data = event_review_service.get_event_detail(event_id)
    except event_review_service.EventReviewConfigError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="告警事件不存在或已不在当前查询范围")
    return OkResp[EventReviewDetail](data=data)


@router.post(
    "/claim",
    response_model=OkRespWithMeta[List[EventReviewItem]],
    status_code=http_status.HTTP_200_OK,
)
async def claim_events(
    cmd: EventReviewClaimCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    try:
        data = event_review_service.claim_events(count=cmd.count)
    except event_review_service.EventReviewConfigError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return OkRespWithMeta[List[EventReviewItem]](
        data=data,
        meta_data=MetaData(total=len(data), page=0, size=len(data)),
    )


@router.post(
    "/{event_id}/action",
    response_model=OkResp[EventReviewItem],
    status_code=http_status.HTTP_200_OK,
)
async def perform_action(
    event_id: int,
    cmd: EventReviewActionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    try:
        data = event_review_service.perform_action(
            event_id=event_id,
            action=cmd.action,
            event_type=cmd.event_type,
            remark=cmd.remark,
        )
    except event_review_service.EventReviewConfigError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if data is None:
        raise HTTPException(status_code=400, detail="审核操作失败")
    return OkResp[EventReviewItem](data=data)
