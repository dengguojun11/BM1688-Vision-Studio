from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from labelu.internal.common.db import SessionLocal
from labelu.internal.domain.models.third_party_call_audit import ThirdPartyCallAudit


@dataclass(frozen=True)
class ThirdPartyAuditTarget:
    operation: str
    default_source_system: str


_PATH_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "/internal/v1/experiment-datasets",
        "experiment_dataset_import",
        "strategy_evolution",
    ),
    (
        "/internal/v1/strategy-evolution",
        "strategy_evolution",
        "strategy_evolution",
    ),
    (
        "/api/v1/integrations/strategy-tasks",
        "strategy_task_import",
        "strategy_evolution",
    ),
    (
        "/api/v1/strategy-tasks",
        "strategy_evolution",
        "strategy_evolution",
    ),
    (
        "/api/v1/strategy-evolution/platform-callbacks",
        "strategy_platform_callback",
        "strategy_evolution",
    ),
)


def classify_third_party_call(
    *,
    path: str,
    method: str,
    authorization: str = "",
) -> ThirdPartyAuditTarget | None:
    normalized_path = str(path or "").rstrip("/") or "/"
    normalized_method = str(method or "GET").upper()
    for prefix, operation, default_source in _PATH_RULES:
        if normalized_path != prefix and not normalized_path.startswith(prefix + "/"):
            continue
        # Authenticated GETs are LabelU operator views, not external polling.
        if normalized_method in {"GET", "HEAD"} and str(authorization or "").strip():
            return None
        if operation == "strategy_evolution":
            if normalized_path.endswith("/threshold-experiments"):
                operation = "threshold_experiment"
            elif normalized_path.endswith("/model-trainings"):
                operation = "model_capability"
            elif normalized_method in {"GET", "HEAD"}:
                operation = "strategy_status_query"
            else:
                operation = "strategy_evolution_task"
        return ThirdPartyAuditTarget(operation, default_source)
    return None


def _payload_object(raw_body: bytes) -> dict[str, Any]:
    if not raw_body or len(raw_body) > 2 * 1024 * 1024:
        return {}
    try:
        value = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def extract_third_party_identifiers(
    raw_body: bytes,
    *,
    default_source_system: str,
) -> dict[str, str | int | None]:
    payload = _payload_object(raw_body)
    source_system = (
        str(
            payload.get("source_system")
            or payload.get("external_platform")
            or default_source_system
            or "unknown"
        )
        .strip()
        .lower()
    )
    external_task_id = str(
        payload.get("external_task_id")
        or payload.get("task_id")
        or payload.get("taskId")
        or ""
    ).strip()
    external_package_id = str(
        payload.get("package_id")
        or payload.get("packageId")
        or payload.get("source_package_id")
        or ""
    ).strip()
    schema_version = str(
        payload.get("schema_version") or payload.get("schemaVersion") or ""
    ).strip()
    project_id: int | None = None
    raw_project_id = payload.get("project_id") or payload.get("projectId")
    try:
        if raw_project_id is not None and int(raw_project_id) > 0:
            project_id = int(raw_project_id)
    except (TypeError, ValueError):
        project_id = None
    result = {
        "source_system": source_system or "unknown",
        "external_task_id": external_task_id or None,
        "external_package_id": external_package_id or None,
        "schema_version": schema_version or None,
    }
    if project_id is not None:
        result["project_id"] = project_id
    return result


def sanitize_audit_error_summary(value: str | None) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "请求处理异常，详细信息请按 request_id 查询服务日志"
    if not isinstance(payload, dict):
        return "请求处理失败"
    safe: dict[str, Any] = {}
    for key in ("msg", "message", "detail", "err_code", "status"):
        item = payload.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            if item is not None:
                safe[key] = item
    errors = payload.get("errors")
    if isinstance(errors, list):
        safe["errors"] = [
            {
                key: item.get(key)
                for key in ("type", "loc", "msg")
                if item.get(key) is not None
            }
            for item in errors[:20]
            if isinstance(item, dict)
        ]
    return json.dumps(safe or {"message": "请求处理失败"}, ensure_ascii=False)[:2000]


def persist_third_party_call(
    *,
    target: ThirdPartyAuditTarget,
    request_id: str,
    method: str,
    path: str,
    raw_body: bytes,
    response_status: int,
    duration_ms: int,
    client_ip: str | None,
    user_agent: str | None,
    error_summary: str | None = None,
) -> None:
    identifiers = extract_third_party_identifiers(
        raw_body,
        default_source_system=target.default_source_system,
    )
    if response_status >= 500:
        outcome = "failed"
    elif response_status >= 400:
        outcome = "rejected"
    elif response_status == 202:
        outcome = "accepted"
    else:
        outcome = "succeeded"
    db = SessionLocal()
    try:
        # Third-party strategy/evolution data is global in一期.  The legacy
        # audit table still has a nullable ``project_id`` column for old rows,
        # but current requests must never be allowed to create a project
        # boundary or spoof ownership through ``project_id``/``projectId``.
        # Keep the parsed identifier available to compatibility callers while
        # deliberately discarding it at persistence time.
        project_id = None
        db.add(
            ThirdPartyCallAudit(
                request_id=str(request_id or "-")[:64],
                project_id=project_id,
                source_system=str(identifiers["source_system"] or "unknown")[:64],
                operation=target.operation[:64],
                request_method=str(method or "GET").upper()[:16],
                request_path=str(path or "/")[:512],
                schema_version=(
                    str(identifiers["schema_version"])[:32]
                    if identifiers["schema_version"]
                    else None
                ),
                external_task_id=(
                    str(identifiers["external_task_id"])[:128]
                    if identifiers["external_task_id"]
                    else None
                ),
                external_package_id=(
                    str(identifiers["external_package_id"])[:128]
                    if identifiers["external_package_id"]
                    else None
                ),
                request_sha256=hashlib.sha256(raw_body or b"").hexdigest(),
                request_bytes=len(raw_body or b""),
                response_status=int(response_status),
                outcome=outcome,
                duration_ms=max(0, int(duration_ms or 0)),
                client_ip=str(client_ip or "")[:64] or None,
                user_agent=str(user_agent or "")[:512] or None,
                error_summary=sanitize_audit_error_summary(error_summary),
                received_at=datetime.now(),
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(
            "third-party call audit persist failed request_id={} error={}",
            request_id,
            exc,
        )
    finally:
        db.close()
