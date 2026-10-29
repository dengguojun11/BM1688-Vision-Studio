from typing import List
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials

from labelu.internal.common import db as db_module
from labelu.internal.domain.models.user import User
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import user as service
from labelu.internal.application.command.user import LoginCommand
from labelu.internal.application.command.user import SignupCommand
from labelu.internal.application.response.user import LoginResponse
from labelu.internal.application.response.user import LogoutResponse
from labelu.internal.application.response.user import AboutResponse
from labelu.internal.application.response.user import UserResponse
from labelu.internal.common.config import settings
from labelu.internal.common.labelu_frontend_integration import read_frontend_release_version
from labelu.version import version as labelu_version

router = APIRouter(prefix="/users", tags=["users"])

@router.get('', response_model=OkRespWithMeta[List[UserResponse]], status_code=status.HTTP_200_OK)
async def list_users(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(db_module.get_db),
    page: int | None = 0,
    size: int | None = 10,
    username: str | None = None,
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按用户 ID、账号或账号类型搜索",
    ),
):
    """
    List all users
    """

    # business logic
    data, total = await service.list_by(
        db=db, page=page, size=size, username=username, search=search
    )
    
    meta_data = MetaData(total=total, page=page, size=len(data))

    # response
    return OkRespWithMeta[List[UserResponse]](meta_data=meta_data, data=data)

@router.get('/me', response_model=OkResp[UserResponse], status_code=status.HTTP_200_OK)
async def get_me(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    """
    Get current user information
    """

    # response
    from labelu.internal.common.task_access import is_task_superuser

    return OkResp[UserResponse](data=UserResponse(
        id=current_user.id,
        username=current_user.username,
        is_task_superuser=is_task_superuser(current_user),
    ))


@router.get('/about', response_model=OkResp[AboutResponse], status_code=status.HTTP_200_OK)
async def about():
    """部署探针：无需登录，用于确认 pip 发版是否包含标注统计相关字段。"""
    return OkResp[AboutResponse](
        data=AboutResponse(
            version=labelu_version,
            frontend_version=read_frontend_release_version(),
            user_me_has_superuser_field='is_task_superuser' in UserResponse.model_fields,
            task_superuser_usernames_configured=bool(
                (settings.TASK_SUPERUSER_USERNAMES or '').strip()
            ),
        )
    )


@router.post(
    "/signup",
    response_model=OkResp[UserResponse],
    status_code=status.HTTP_201_CREATED,
)
async def signup(cmd: SignupCommand, db: Session = Depends(db_module.get_db)):
    """
    Create a user account and return the created account.

    The service performs the duplicate-username check and hashes the password
    before persisting it. Authentication is still required for protected
    platform operations after registration.
    """
    data = await service.signup(db=db, cmd=cmd)
    return OkResp[UserResponse](data=data)


@router.post(
    "/login",
    response_model=OkResp[LoginResponse],
    status_code=status.HTTP_200_OK,
)
async def login(cmd: LoginCommand, db: Session = Depends(db_module.get_db)):
    """
    User login, get an access token for future requests
    """

    # business logic
    data = await service.login(db=db, cmd=cmd)

    # response
    return OkResp[LoginResponse](data=data)


@router.post(
    "/logout",
    response_model=OkResp[LogoutResponse],
    status_code=status.HTTP_200_OK,
)
async def logout(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    User logout.

    Note: With JWT token expiration enabled (30min TTL), server-side
    token invalidation is not strictly necessary. The client should
    discard the token on logout. For stricter security, implement
    a token blacklist with Redis.
    """

    # response
    data = LogoutResponse(msg="succeeded")
    return OkResp[LogoutResponse](data=data)

