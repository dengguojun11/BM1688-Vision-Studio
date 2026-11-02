"""Lightweight user/system operation journal.

User-facing logs answer "what happened" with a short action label; request
details are kept in a JSON payload for the detail drawer.  The journal must
never break the business request that it observes, so persistence failures are
only logged.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import or_

from labelu.internal.common.db import SessionLocal
from labelu.internal.domain.models.system_operation_log import SystemOperationLog
from labelu.internal.domain.models.user import User

CATEGORY_USER = "USER"
CATEGORY_SYSTEM = "SYSTEM"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_SKIP_PATH_FRAGMENTS = (
    "/users/login",
    "/users/register",
    "/users/password",
    "/system-logs",
    "/local-dev/",
)
_RESOURCE_LABELS = {
    "tasks": "任务",
    "samples": "样本",
    "attachments": "附件",
    "training": "训练",
    "yolo": "训练",
    "models": "模型",
    "model-version": "模型",
    "evolution": "进化中心",
    "integrations": "第三方集成",
    "admin": "系统管理",
    "users": "账号",
    "annotation-taxonomy": "标签体系",
    "annotation-settings": "标注设置",
    "datasets": "数据集",
    "ingestion": "数据导入",
    "providers": "模型接入",
    "projects": "项目",
    "organizations": "组织",
    "workflows": "流程",
    "edge-devices": "边端设备",
    "rules": "规则",
    "export": "导出",
    "integration-settings": "集成设置",
    "experiment-datasets": "专家回流数据",
    "board-agent": "边端代理",
}
_ACTION_BY_METHOD = {
    "POST": "创建",
    "PUT": "更新",
    "PATCH": "更新",
    "DELETE": "删除",
}


def normalize_category(value: str | None) -> str:
    return CATEGORY_SYSTEM if str(value or "").upper() == CATEGORY_SYSTEM else CATEGORY_USER


def _should_skip(path: str) -> bool:
    return any(fragment in path for fragment in _SKIP_PATH_FRAGMENTS)


def classify_request_operation(
    *, path: str, method: str, user_id: int | None
) -> str | None:
    """Return USER/SYSTEM for a mutating request, or None when it is noise."""
    if method.upper() not in MUTATING_METHODS or _should_skip(path):
        return None
    if path.startswith("/internal/") or path.startswith("/api/v1/internal/"):
        return CATEGORY_SYSTEM
    # Unauthenticated callbacks/registration flows are system-originated.
    if user_id is None:
        return CATEGORY_SYSTEM
    return CATEGORY_USER


def describe_request_action(*, path: str, method: str) -> str:
    """Build the short Chinese action shown in the list."""
    method = method.upper()
    path = re.sub(r"/+$", "", path)
    segments = [part for part in path.split("/") if part]
    resource = next(
        (part for part in segments if part in _RESOURCE_LABELS),
        segments[2] if len(segments) > 2 else "",
    )
    resource_label = _RESOURCE_LABELS.get(resource, resource or "系统")

    if path.endswith("/auto_label"):
        return "AI 预标注"
    if "/samples/" in path and method == "PATCH":
        return "保存样本标注"
    if path.endswith("/retry") and "/training/jobs/" in path:
        return "重试训练作业"
    if path.endswith("/pause") and "/training/jobs/" in path:
        return "暂停训练作业"
    if path.endswith("/cancel"):
        return f"取消{resource_label}"
    if "/publish" in path and method in {"POST", "PUT", "PATCH"}:
        return f"发布{resource_label}"
    if "/upload" in path and method == "POST":
        return f"上传{resource_label}"
    if "/import" in path and method == "POST":
        return f"导入{resource_label}"

    verb = _ACTION_BY_METHOD.get(method, "操作")
    return f"{verb}{resource_label}"


def _short_body_summary(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw[:4000]


def _detail_body(raw: str | None):
    if not raw:
        return None
    trimmed = _short_body_summary(raw)
    try:
        return json.loads(trimmed) if trimmed else None
    except (TypeError, ValueError):
        return trimmed


def persist_request_operation_log(
    *,
    category: str,
    action: str,
    summary: str | None = None,
    user_id: int | None = None,
    source: str | None = None,
    request_id: str | None = None,
    method: str,
    path: str,
    status_code: int | None = None,
    duration_ms: int = 0,
    query: dict[str, Any] | None = None,
    body_summary: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    error: str | None = None,
) -> None:
    """Persist one operation without affecting the request path."""
    db = SessionLocal()
    try:
        username = None
        if user_id is not None:
            user = db.query(User).filter(User.id == int(user_id)).first()
            if user is not None:
                username = user.username

        detail = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "query": query or {},
            "body": _detail_body(body_summary),
            "client_ip": client_ip,
            "user_agent": user_agent,
            "error": error,
        }
        db.add(
            SystemOperationLog(
                category=category,
                action=action,
                summary=summary or action,
                user_id=user_id,
                username=username,
                source=source or ("web" if category == CATEGORY_USER else "system"),
                request_id=request_id,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=max(0, int(duration_ms or 0)),
                detail=json.dumps(detail, ensure_ascii=False, default=str),
                created_at=datetime.now(),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("persist system operation log failed path={} method={}", path, method)
    finally:
        db.close()


def record_system_operation(
    *,
    action: str,
    summary: str | None = None,
    detail: dict[str, Any] | None = None,
    source: str = "worker",
) -> None:
    """Record an operation generated by a worker/callback without HTTP."""
    persist_request_operation_log(
        category=CATEGORY_SYSTEM,
        action=action,
        summary=summary,
        source=source,
        method="SYSTEM",
        path=source,
        status_code=None,
        body_summary=json.dumps(detail or {}, ensure_ascii=False, default=str),
    )


def list_operation_logs(
    db,
    *,
    category: str,
    page: int = 0,
    size: int = 20,
    q: str | None = None,
):
    normalized = normalize_category(category)
    query = db.query(SystemOperationLog).filter(SystemOperationLog.category == normalized)
    keyword = str(q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                SystemOperationLog.action.like(like),
                SystemOperationLog.summary.like(like),
                SystemOperationLog.username.like(like),
                SystemOperationLog.path.like(like),
            )
        )
    total = query.count()
    rows = (
        query.order_by(SystemOperationLog.created_at.desc(), SystemOperationLog.id.desc())
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, min(int(size), 100)))
        .all()
    )
    return rows, total


def get_operation_log(db, log_id: int):
    return db.query(SystemOperationLog).filter(SystemOperationLog.id == int(log_id)).first()


def parse_detail(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except (TypeError, ValueError):
        return {"raw": raw}
