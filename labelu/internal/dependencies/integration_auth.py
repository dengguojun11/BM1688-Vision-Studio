from __future__ import annotations

import hmac

from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from labelu.internal.common import db as db_module
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.service_account import ensure_open_system_user
from labelu.internal.common.third_party_auth import verify_third_party_request
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

SERVICE_TOKEN_HEADERS = (
    "X-7GAI-Service-Token",
    "X-LabelU-Service-Token",
    "X-Service-Token",
)


def _service_token() -> str:
    return str(getattr(settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "") or "").strip()


def _presented_service_token(request: Request) -> str:
    for name in SERVICE_TOKEN_HEADERS:
        value = str(request.headers.get(name) or "").strip()
        if value:
            return value
    authorization = str(request.headers.get("Authorization") or "").strip()
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.lower().lower() == "bearer":
        return value.strip()
    return ""


async def get_integration_actor(
    request: Request,
    db: Session = Depends(db_module.get_db),
) -> User:
    """Accept a user JWT, the internal service token, or the generic HMAC contract.

    The public v2 integration contract is used both by the browser console and
    by the third-party service.  Keeping both credential types in one dependency
    avoids maintaining two sets of endpoints while preserving the existing
    HMAC timestamp/nonce/replay protection.
    """
    configured = _service_token()
    presented = _presented_service_token(request)
    if configured and presented and hmac.compare_digest(presented, configured):
        return ensure_open_system_user(db)
    if configured and presented and not str(request.headers.get("Authorization") or "").strip():
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_401_UNAUTHORIZED,
            message="第三方服务令牌无效",
        )

    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            try:
                return get_current_user(db=db, token=token)
            except LabelUException:
                if configured:
                    raise
                raise LabelUException(
                    code=ErrorCode.CODE_40003_CREDENTIAL_ERROR,
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    message="请求凭据无效",
                )

    signature_headers = any(
        str(request.headers.get(name) or "").strip()
        for name in (
            "X-LabelU-Timestamp",
            "X-LabelU-Nonce",
            "X-LabelU-Signature",
        )
    )
    if signature_headers:
        await verify_third_party_request(
            request,
            required=True,
            db=db,
            source_system="integration_requests",
        )
        return ensure_open_system_user(db)

    raise LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_401_UNAUTHORIZED,
        message="第三方接口需要用户令牌、服务令牌或 HMAC 签名",
    )
