"""Short-lived token for GT-Pred compare page single-sample access."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt

from labelu.internal.common.config import settings

PURPOSE = "compare_sample_access"
DEFAULT_TTL_HOURS = 12


def create_compare_sample_access_token(
    task_id: int,
    sample_id: int,
    *,
    job_id: int | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=int(ttl_hours))
    payload: Dict[str, Any] = {
        "purpose": PURPOSE,
        "task_id": int(task_id),
        "sample_id": int(sample_id),
        "exp": expire,
    }
    if job_id is not None:
        payload["job_id"] = int(job_id)
    return jwt.encode(
        payload,
        settings.PASSWORD_SECRET_KEY,
        algorithm=settings.TOKEN_GENERATE_ALGORITHM,
    )


def verify_compare_sample_access_token(
    token: str,
    *,
    task_id: int,
    sample_id: int,
    job_id: int | None = None,
) -> bool:
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
        if int(data.get("task_id")) != int(task_id):
            return False
        if int(data.get("sample_id")) != int(sample_id):
            return False
        if job_id is not None and int(data.get("job_id")) != int(job_id):
            return False
    except (TypeError, ValueError):
        return False
    return True
