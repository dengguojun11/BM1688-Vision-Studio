from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import personnel_statistics
from labelu.internal.application.service import user_activity
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter()


class HeartbeatCommand(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=128)
    path: Optional[str] = Field(default=None, max_length=512)


class HeartbeatResponse(BaseModel):
    session_id: str
    last_seen_at: datetime
    online_window_seconds: int = user_activity.ONLINE_WINDOW_SECONDS


class LogoutCommand(BaseModel):
    session_id: Optional[str] = None


class HeartbeatLogoutResponse(BaseModel):
    ok: bool = True


class PersonnelSummary(BaseModel):
    date_from: date
    date_to: date
    online_now: int = 0
    active_users: int = 0
    annotation_images: int = 0
    annotation_boxes: int = 0
    annotation_edits: int = 0
    review_images: int = 0
    review_approved: int = 0
    review_rejected: int = 0
    review_arbitrated: int = 0
    review_invalidated: int = 0
    effective_work_seconds: int = 0


class PersonnelUserRow(BaseModel):
    user_id: int
    username: str
    online: bool = False
    last_seen_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    last_business_at: Optional[datetime] = None
    online_seconds: int = 0
    effective_work_seconds: int = 0
    annotation_images: int = 0
    annotation_boxes: int = 0
    annotation_edits: int = 0
    review_images: int = 0
    review_approved: int = 0
    review_rejected: int = 0
    review_arbitrated: int = 0
    review_invalidated: int = 0
    task_count: int = 0


class PersonnelDashboardResponse(BaseModel):
    summary: PersonnelSummary
    users: List[PersonnelUserRow] = Field(default_factory=list)


class PersonnelDailyRow(BaseModel):
    date: date
    annotation_images: int = 0
    annotation_boxes: int = 0
    annotation_edits: int = 0
    review_images: int = 0
    review_approved: int = 0
    review_rejected: int = 0
    review_arbitrated: int = 0
    review_invalidated: int = 0


class PersonnelDetailResponse(BaseModel):
    user: Optional[PersonnelUserRow] = None
    daily: List[PersonnelDailyRow] = Field(default_factory=list)


class OnlineUserRow(BaseModel):
    user_id: int
    username: str
    login_at: datetime
    last_seen_at: datetime
    last_path: Optional[str] = None
    ip: Optional[str] = None
    session_id: str


class OnlineUsersResponse(BaseModel):
    online_window_seconds: int
    users: List[OnlineUserRow] = Field(default_factory=list)


@router.post(
    "/user-activity/heartbeat",
    response_model=OkResp[HeartbeatResponse],
    status_code=status.HTTP_200_OK,
)
def heartbeat(
    cmd: HeartbeatCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    row = user_activity.record_heartbeat(
        db,
        user=current_user,
        session_id=cmd.session_id,
        path=cmd.path,
    )
    return OkResp[HeartbeatResponse](
        data=HeartbeatResponse(
            session_id=row.session_id,
            last_seen_at=row.last_seen_at,
        )
    )


@router.post(
    "/user-activity/logout",
    response_model=OkResp[HeartbeatLogoutResponse],
    status_code=status.HTTP_200_OK,
)
def logout_activity(
    cmd: LogoutCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    user_activity.mark_logout(db, user=current_user, session_id=cmd.session_id)
    return OkResp[HeartbeatLogoutResponse](data=HeartbeatLogoutResponse())


@router.get(
    "/personnel-statistics/dashboard",
    response_model=OkResp[PersonnelDashboardResponse],
    status_code=status.HTTP_200_OK,
)
def personnel_dashboard(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    user_id: Optional[int] = Query(None, gt=0),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=6))
    if start > end:
        start, end = end, start
    data = personnel_statistics.get_personnel_dashboard(
        db,
        current_user,
        date_from=start,
        date_to=end,
        user_id=user_id,
    )
    return OkResp[PersonnelDashboardResponse](
        data=PersonnelDashboardResponse(**data)
    )


@router.get(
    "/personnel-statistics/users/{user_id}",
    response_model=OkResp[PersonnelDetailResponse],
    status_code=status.HTTP_200_OK,
)
def personnel_user_detail(
    user_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=6))
    if start > end:
        start, end = end, start
    data = personnel_statistics.get_personnel_detail(
        db,
        current_user,
        user_id=user_id,
        date_from=start,
        date_to=end,
    )
    return OkResp[PersonnelDetailResponse](
        data=PersonnelDetailResponse(**data)
    )


@router.get(
    "/personnel-statistics/online",
    response_model=OkResp[OnlineUsersResponse],
    status_code=status.HTTP_200_OK,
)
def personnel_online(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    from labelu.internal.common.task_access import is_task_superuser

    if not is_task_superuser(current_user):
        from labelu.internal.common.error_code import ErrorCode, LabelUException

        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅管理人员可查看在线状态",
        )
    users = user_activity.online_users(db)
    return OkResp[OnlineUsersResponse](
        data=OnlineUsersResponse(
            online_window_seconds=user_activity.ONLINE_WINDOW_SECONDS,
            users=[OnlineUserRow(**item) for item in users],
        )
    )
