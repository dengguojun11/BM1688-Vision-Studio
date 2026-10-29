"""Structured business / request logging helpers (loguru)."""

from __future__ import annotations

import contextvars
import json
import uuid
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from jose import jwt
from loguru import logger

from labelu.internal.common.config import settings

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
user_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default="-"
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "access_key",
        "secret_access_key",
        "access_key_id",
        "api_key",
        "credential",
    }
)

_SKIP_LOG_KWARGS = frozenset(
    {"db", "session", "current_user", "user", "cmd", "request"}
)

F = TypeVar("F", bound=Callable[..., Any])


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def set_request_context(
    *, request_id: str, user_id: Optional[int | str] = None
) -> None:
    request_id_ctx.set(request_id)
    user_id_ctx.set(str(user_id) if user_id is not None else "-")


def get_request_context() -> dict[str, str]:
    return {"request_id": request_id_ctx.get(), "user_id": user_id_ctx.get()}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEYS)


def _sanitize_value(key: str, value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if _is_sensitive_key(key):
        return "***"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 200:
            return value[:200] + "…"
        return value
    if isinstance(value, (list, tuple)):
        items = [_sanitize_value(key, item, depth + 1) for item in value[:20]]
        if len(value) > 20:
            items.append(f"…+{len(value) - 20}")
        return items
    if isinstance(value, dict):
        return {
            k: _sanitize_value(str(k), v, depth + 1)
            for k, v in list(value.items())[:30]
        }
    return str(value)[:200]


def sanitize_mapping(data: Any) -> Any:
    if not isinstance(data, dict):
        return _sanitize_value("body", data)
    return {str(k): _sanitize_value(str(k), v) for k, v in data.items()}


def should_log_http_payload(path: str) -> bool:
    """Whether to log request/response JSON for this path."""
    if not settings.LOG_HTTP_BODY:
        return False
    if not path.startswith("/api/"):
        return False
    lowered = path.lower()
    skip_fragments = (
        "/download",
        "/partial-content",
        "/export/",
        "/media/",
    )
    return not any(fragment in lowered for fragment in skip_fragments)


def summarize_http_payload(
    raw: bytes | None,
    *,
    content_type: str = "",
    max_len: int | None = None,
    max_capture_bytes: int | None = None,
) -> str:
    """Format HTTP body bytes for logs (JSON sanitized, binary abbreviated)."""
    if not raw:
        return ""
    capture_limit = (
        max_capture_bytes
        if max_capture_bytes is not None
        else settings.LOG_HTTP_BODY_MAX_CAPTURE_BYTES
    )
    if len(raw) > capture_limit:
        return f"<body len={len(raw)} omitted>"
    limit = max_len if max_len is not None else settings.LOG_HTTP_BODY_MAX_CHARS
    content_type_lower = (content_type or "").lower()
    if (
        "image/" in content_type_lower
        or "octet-stream" in content_type_lower
        or "video/" in content_type_lower
    ):
        return f"<binary len={len(raw)}>"
    if raw[:4] == b"%PDF" or raw[:2] == b"\xff\xd8":
        return f"<binary len={len(raw)}>"

    if "application/json" in content_type_lower or raw[:1] in (b"{", b"["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return summarize_for_log(sanitize_mapping(parsed), max_len=limit)
            return summarize_for_log(parsed, max_len=limit)
        except Exception:
            pass

    try:
        text = raw.decode("utf-8")
    except Exception:
        return f"<binary len={len(raw)}>"
    return summarize_for_log(text, max_len=limit)


def summarize_for_log(data: Any, *, max_len: int = 500) -> str:
    try:
        if isinstance(data, (dict, list, tuple)):
            text = json.dumps(
                sanitize_mapping(data) if isinstance(data, dict) else data,
                ensure_ascii=False,
            )
        else:
            text = str(data)
    except Exception:
        text = repr(data)
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def escape_log_format_braces(text: str) -> str:
    """Prevent loguru dynamic formatters from treating `{param}` in messages as placeholders."""
    return text.replace("{", "{{").replace("}", "}}")


def escape_log_format_tags(text: str) -> str:
    """Prevent Loguru from treating dynamic text enclosed in angle brackets as markup."""
    return text.replace("<", r"\<")


def resolve_route_path_for_log(route_path: str, fields: dict[str, Any]) -> str:
    """Replace FastAPI path params in route templates for readable log output."""
    resolved = route_path
    for key, value in fields.items():
        if value is None:
            continue
        resolved = resolved.replace(f"{{{key}}}", str(value))
    return resolved


def peek_user_id_from_authorization(authorization: str) -> Optional[int]:
    """Decode JWT for logging only (signature verified, expiry ignored)."""
    if not authorization:
        return None
    _, _, token = authorization.partition(" ")
    token = token.strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.PASSWORD_SECRET_KEY,
            algorithms=[settings.TOKEN_GENERATE_ALGORITHM],
            options={"verify_signature": True, "verify_exp": False},
        )
        user_id = payload.get("id")
        return int(user_id) if user_id is not None else None
    except Exception:
        return None


def biz_log(event: str, /, **fields: Any) -> None:
    if not settings.LOG_BUSINESS_DETAIL:
        return
    extra = {
        **get_request_context(),
        "task_id": "-",
        "sample_id": "-",
        "job_id": "-",
        "export_id": "-",
        "file_path": "-",
        "device_id": "-",
    }
    for key, value in fields.items():
        if value is None:
            continue
        if key.endswith("_id") or key in {
            "task_id",
            "sample_id",
            "job_id",
            "export_id",
            "file_path",
            "device_id",
            "kind",
            "filename",
        }:
            extra[key] = value
    parts = " ".join(
        f"{key}={summarize_for_log(value, max_len=300)}"
        for key, value in fields.items()
        if value is not None
    )
    logger.bind(**extra).info("[biz] {}{}", event, f" {parts}" if parts else "")


def instrument_sync_service_module(module) -> None:
    """Wrap public sync service functions (e.g. get_batch_job, background runners)."""
    import inspect

    mod_name = module.__name__
    short_name = mod_name.rsplit(".", 1)[-1]

    for name, obj in list(vars(module).items()):
        if name.startswith("_"):
            continue
        if not inspect.isfunction(obj):
            continue
        if getattr(obj, "__module__", None) != mod_name:
            continue
        if inspect.iscoroutinefunction(obj):
            continue
        if getattr(obj, "__wrapped_log_business__", False):
            continue
        wrapped = log_business_operation(f"{short_name}.{name}")(obj)
        setattr(module, name, wrapped)


def _extract_loggable_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _SKIP_LOG_KWARGS:
            if key == "current_user" and value is not None:
                out["user_id"] = getattr(value, "id", None)
            continue
        if key.endswith("_id") or key in {
            "task_id",
            "sample_id",
            "job_id",
            "export_id",
            "file_path",
            "device_id",
            "kind",
            "filename",
        }:
            out[key] = value
    return out


def log_business_operation(operation: str) -> Callable[[F], F]:
    """Log service entry/exit; use together with inline biz_log() for steps."""

    def decorator(func: F) -> F:
        if getattr(func, "__wrapped_log_business__", False):
            return func

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            fields = _extract_loggable_kwargs(kwargs)
            biz_log(f"{operation}.start", **fields)
            try:
                result = await func(*args, **kwargs)
                biz_log(f"{operation}.done", **fields)
                return result
            except Exception as exc:
                biz_log(f"{operation}.failed", error=type(exc).__name__, **fields)
                raise

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            fields = _extract_loggable_kwargs(kwargs)
            biz_log(f"{operation}.start", **fields)
            try:
                result = func(*args, **kwargs)
                biz_log(f"{operation}.done", **fields)
                return result
            except Exception as exc:
                biz_log(f"{operation}.failed", error=type(exc).__name__, **fields)
                raise

        import asyncio

        wrapped: Callable[..., Any]
        if asyncio.iscoroutinefunction(func):
            wrapped = async_wrapper
        else:
            wrapped = sync_wrapper
        wrapped.__wrapped_log_business__ = True  # type: ignore[attr-defined]
        return wrapped  # type: ignore[return-value]

    return decorator
