from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import OkResp, OkRespWithMeta
from labelu.internal.application.service import system_operation_log as service
from labelu.internal.common import db as db_module
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User
from labelu.internal.common.security import security

router = APIRouter()


class SystemOperationLogItem(BaseModel):
    id: int
    category: str
    action: str
    summary: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    source: Optional[str] = None
    method: str
    path: str
    status_code: Optional[int] = None
    duration_ms: int = 0
    created_at: datetime


class SystemOperationLogDetail(SystemOperationLogItem):
    request_id: Optional[str] = None
    detail: Dict[str, Any] = Field(default_factory=dict)


def _item(row) -> SystemOperationLogItem:
    return SystemOperationLogItem(
        id=row.id,
        category=row.category,
        action=row.action,
        summary=row.summary,
        user_id=row.user_id,
        username=row.username,
        source=row.source,
        method=row.method,
        path=row.path,
        status_code=row.status_code,
        duration_ms=int(row.duration_ms or 0),
        created_at=row.created_at,
    )


@router.get(
    "/system-logs",
    response_model=OkRespWithMeta[List[SystemOperationLogItem]],
    status_code=status.HTTP_200_OK,
)
async def list_system_logs(
    category: str = Query("user"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    rows, total = service.list_operation_logs(
        db, category=category, page=page, size=size, q=q
    )
    return OkRespWithMeta[List[SystemOperationLogItem]](
        data=[_item(row) for row in rows],
        meta_data={"total": total, "page": page, "size": size},
    )


@router.get(
    "/system-logs/{log_id}",
    response_model=OkResp[SystemOperationLogDetail],
    status_code=status.HTTP_200_OK,
)
async def get_system_log(
    log_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    row = service.get_operation_log(db, log_id)
    if row is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="操作日志不存在",
        )

    item = _item(row)
    return OkResp[SystemOperationLogDetail](
        data=SystemOperationLogDetail(
            **item.model_dump(),
            request_id=row.request_id,
            detail=service.parse_detail(row.detail),
        )
    )
