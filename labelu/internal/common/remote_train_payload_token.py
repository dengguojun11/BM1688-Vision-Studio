"""远程训练 payload 下载短时 token（节点 HTTP 拉取，无需用户 JWT）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt

from labelu.internal.common.config import settings

PURPOSE = "yolo_remote_payload"
DEFAULT_TTL_HOURS = 72


def create_remote_payload_token(job_id: int, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=int(ttl_hours))
    payload: Dict[str, Any] = {
        "job_id": int(job_id),
        "purpose": PURPOSE,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        settings.PASSWORD_SECRET_KEY,
        algorithm=settings.TOKEN_GENERATE_ALGORITHM,
    )


def verify_remote_payload_token(token: str, job_id: int) -> bool:
    if not (token or "").strip():
        return False
    try:
        data = jwt.decode(
            token,
            settings.PASSWORD_SECRET_KEY,
            algorithms=[settings.TOKEN_GENERATE_ALGORITHM],
        )
    except JWTError:
        return False
    if not isinstance(data, dict):
        return False
    if str(data.get("purpose") or "") != PURPOSE:
        return False
    try:
        return int(data.get("job_id")) == int(job_id)
    except (TypeError, ValueError):
        return False
