from typing import Optional

from jose import jwt
from loguru import logger
from sqlalchemy.orm import Session
from pydantic import ValidationError
from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status

from labelu.internal.domain.models.user import User
from labelu.internal.common import db as db_module
from labelu.internal.common.config import settings
from labelu.internal.common.security import AccessToken
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import LabelUException
from labelu.internal.adapter.persistence import crud_user


class SpecialOAuth2PasswordBearer:
    def __call__(self, request: Request) -> Optional[str]:
        authorization: str = request.headers.get("Authorization", "")
        if not authorization:
            raise LabelUException(
                code=ErrorCode.CODE_40003_CREDENTIAL_ERROR,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        _, _, param = authorization.partition(" ")
        return param


reusable_oauth2 = SpecialOAuth2PasswordBearer()


def get_current_user(
    db: Session = Depends(db_module.get_db), token: str = Depends(reusable_oauth2)
) -> User:
    try:
        payload = jwt.decode(
            token,
            settings.PASSWORD_SECRET_KEY,
            algorithms=[settings.TOKEN_GENERATE_ALGORITHM],
            options={"verify_exp": True},
        )
        token_data = AccessToken(**payload)
    except (jwt.JWTError, ValidationError):
        raise LabelUException(
            code=ErrorCode.CODE_40003_CREDENTIAL_ERROR,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    user = crud_user.get(db, id=token_data.id)
    if not user:
        raise LabelUException(code=ErrorCode.CODE_40002_USER_NOT_FOUND, status_code=401)
    return user


async def verify_ws_token(websocket: WebSocket, db: Session = Depends(db_module.get_db)):
    """WebSocket 鉴权：须使用 WebSocketException，禁止抛 HTTPException（否则会触发错误的 ASGI HTTP 响应）。"""
    token = websocket.query_params.get("token")
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="缺少认证令牌",
        )
    try:
        payload = jwt.decode(
            token,
            settings.PASSWORD_SECRET_KEY,
            algorithms=[settings.TOKEN_GENERATE_ALGORITHM],
            options={"verify_exp": True},
        )
        token_data = AccessToken(**payload)
    except jwt.JWTError as e:
        logger.error("WebSocket authentication error: {}", str(e))
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="令牌无效或已过期（若刚重启过服务，请重新登录：开发环境未固定 PASSWORD_SECRET_KEY 时旧 Token 会验签失败）",
        ) from e
    except ValidationError as e:
        logger.error("WebSocket token payload invalid: {}", str(e))
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="令牌格式无效",
        ) from e

    user = crud_user.get(db, id=token_data.id)
    if not user:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="用户不存在",
        )
    return user


def require_system_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require the configured system-level administrator for global mutations."""
    from labelu.internal.common.task_access import is_task_superuser

    if not is_task_superuser(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅系统管理员可执行此操作",
        )
    return current_user
