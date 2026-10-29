from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta

from fastapi import Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.annotation_taxonomy import ThirdPartyRequestReceipt


async def verify_third_party_request(
    request: Request,
    *,
    required: bool,
    db: Session | None = None,
    source_system: str = "third_party",
) -> None:
    if not required:
        return
    secret = str(settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET or "").strip()
    if not secret:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="第三方接口 HMAC 密钥未配置",
        )
    timestamp_text = str(request.headers.get("X-LabelU-Timestamp") or "").strip()
    nonce = str(request.headers.get("X-LabelU-Nonce") or request.headers.get("X-LabelU-Event-Id") or "").strip()
    signature = str(request.headers.get("X-LabelU-Signature") or "").strip()
    if not timestamp_text or not nonce or not signature:
        raise LabelUException(code=ErrorCode.CODE_30001_NO_PERMISSION, status_code=401, message="v2 第三方请求缺少 HMAC 时间戳、nonce 或签名")
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise LabelUException(code=ErrorCode.CODE_30001_NO_PERMISSION, status_code=401, message="第三方请求时间戳无效") from exc
    if abs(int(time.time()) - timestamp) > int(settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_MAX_SKEW_SECONDS):
        raise LabelUException(code=ErrorCode.CODE_30001_NO_PERMISSION, status_code=401, message="第三方请求签名已过期")
    body = await request.body()
    if len(body) > int(settings.THIRD_PARTY_REQUEST_MAX_BYTES):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            message="第三方请求体超过平台限制",
        )
    body_sha256 = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            request.method.upper(),
            request.url.path,
            timestamp_text,
            nonce,
            body_sha256,
        )
    ).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    provided = signature[7:] if signature.lower().startswith("sha256=") else ""
    if not provided or not hmac.compare_digest(provided.lower(), expected):
        raise LabelUException(code=ErrorCode.CODE_30001_NO_PERMISSION, status_code=401, message="第三方请求 HMAC 签名无效")
    if db is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="第三方防重放存储未配置",
        )

    source = str(source_system or "third_party").strip().lower()
    minute_ago = datetime.now() - timedelta(minutes=1)
    recent_count = db.query(ThirdPartyRequestReceipt.id).filter(
        ThirdPartyRequestReceipt.source_system == source,
        ThirdPartyRequestReceipt.received_at >= minute_ago,
    ).count()
    if recent_count >= int(settings.THIRD_PARTY_RATE_LIMIT_PER_MINUTE):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            message="第三方来源请求过于频繁",
        )
    if db.query(ThirdPartyRequestReceipt.id).filter(
        ThirdPartyRequestReceipt.source_system == source,
        ThirdPartyRequestReceipt.nonce == nonce,
    ).first():
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="第三方请求 nonce 已使用，禁止重放",
        )
    try:
        with begin_transaction(db):
            db.add(
                ThirdPartyRequestReceipt(
                    source_system=source,
                    nonce=nonce,
                    request_method=request.method.upper(),
                    request_path=request.url.path,
                    request_sha256=body_sha256,
                    received_at=datetime.now(),
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="第三方请求 nonce 已使用，禁止重放",
        ) from exc
