"""Authentication for service-to-service internal HTTP endpoints.

Internal routes are not user-facing APIs.  They therefore accept either a
dedicated service token or the existing request-signing contract used by the
strategy-evolution integration.  The legacy unauthenticated mode is explicit
and intended only for tests/controlled protocol migration.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from labelu.internal.common import db as db_module
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.third_party_auth import verify_third_party_request

SERVICE_TOKEN_HEADERS = (
    "X-7GAI-Service-Token",
    "X-LabelU-Service-Token",
    "X-Service-Token",
)

# ``auto_error=False`` keeps HMAC-only callers valid while exposing the
# service credential in generated OpenAPI as an optional security mechanism.
service_token_header = APIKeyHeader(
    name="X-7GAI-Service-Token",
    auto_error=False,
    description=(
        "内部服务令牌。也可使用现有策略演化 HMAC 请求签名；"
        "缺少两种凭据时请求会被拒绝。"
    ),
)


def _configured_service_token() -> str:
    return str(getattr(settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "") or "").strip()


def _presented_service_token(request: Request) -> str:
    for header_name in SERVICE_TOKEN_HEADERS:
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            return value

    # Keep standard clients interoperable while deliberately treating this as
    # a service credential, not as a user JWT.  A JWT is never accepted here
    # unless it exactly equals the configured service token.
    authorization = str(request.headers.get("Authorization") or "").strip()
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        return value.strip()
    return ""


def _unauthorized(message: str = "内部服务接口需要有效的服务令牌或 HMAC 签名") -> None:
    raise LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_401_UNAUTHORIZED,
        message=message,
    )


async def verify_strategy_evolution_internal_request(
    request: Request,
    db: Session = Depends(db_module.get_db),
    _service_token: str | None = Security(service_token_header),
) -> None:
    """Verify a strategy/data-import internal read or retry request.

    A configured token is checked with a constant-time comparison.  HMAC is
    accepted as an alternate credential so existing integration clients can
    migrate without changing their signing implementation.  HMAC requests
    retain timestamp, nonce, rate-limit and replay protections from
    :func:`verify_third_party_request`.
    """
    if not bool(getattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)):
        return

    expected_token = _configured_service_token()
    provided_token = _presented_service_token(request)
    if expected_token and provided_token:
        if hmac.compare_digest(provided_token, expected_token):
            return
        # Do not fall through to HMAC when a caller supplied an invalid token;
        # this avoids confusing credential failures and keeps audit semantics
        # deterministic.  A caller that uses HMAC should omit token headers.
        _unauthorized("内部服务令牌无效")
    elif expected_token and not provided_token:
        # HMAC remains a supported alternative when the signing headers are
        # present, otherwise report the missing service credential below.
        signature_headers_present = any(
            str(request.headers.get(name) or "").strip()
            for name in (
                "X-LabelU-Timestamp",
                "X-LabelU-Nonce",
                "X-LabelU-Event-Id",
                "X-LabelU-Signature",
            )
        )
        if not signature_headers_present:
            _unauthorized()

    secret = str(
        getattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "") or ""
    ).strip()
    if secret:
        # The shared verifier validates the canonical method/path/body hash,
        # timestamp window, nonce replay and per-source rate limit.
        await verify_third_party_request(
            request,
            required=True,
            db=db,
            source_system="strategy_evolution_internal",
        )
        return

    if expected_token:
        _unauthorized()
    raise LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        message="内部服务接口未配置服务令牌或 HMAC 密钥",
    )
