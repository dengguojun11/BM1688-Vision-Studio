from __future__ import annotations

import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import status

from labelu.internal.application.command.model_access_config import (
    CreateModelAccessCommand,
    UpdateModelAccessCommand,
)
from labelu.internal.application.response.model_access_config import (
    ModelAccessConfigBundleResponse,
    ModelAccessConfigResponse,
    ModelAccessConfigTestResponse,
)
from labelu.internal.common import model_access_config as store
from labelu.internal.common.error_code import ErrorCode, LabelUException


# The model-access store is intentionally backend-agnostic: it may be backed
# by the system-config table or a compatibility file depending on deployment.
# Keep list filtering here, after ``store.list_records()`` has normalized the
# records, so search does not couple the API to either persistence backend.
_SEARCH_FIELDS = (
    "id",
    "name",
    "provider_key",
    "capability",
    "endpoint",
    "model",
    "request_protocol",
    "last_check_status",
    "last_check_message",
)
_SEARCH_ALIASES = {
    "vision_llm": "通用视觉模型 自动标注 图文报告",
    "tagtool": "全局标签模型 全局标签预标注",
    "locateanything": "LocateAnything 检测模型 目标检测预标注",
    "vlx_seek": "VLX-Seek 检测模型 目标检测预标注",
    "rex_omni": "Rex-Omni 检测模型 目标检测预标注",
    "volcengine": "火山方舟 Coding Plan 视觉理解",
    "vision_reasoning": "视觉理解 自动标注 图文报告",
    "global_tag": "全局标签预标注",
    "bbox_detection": "目标检测预标注",
}


def _error(exc: ValueError, *, not_found: bool = False) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_400_BAD_REQUEST,
        message=str(exc),
    )


def _response(data: dict) -> ModelAccessConfigResponse:
    return ModelAccessConfigResponse(**store.public_record(data))


def _bundle(rows: list[dict]) -> ModelAccessConfigBundleResponse:
    return ModelAccessConfigBundleResponse(
        configs=[_response(item) for item in rows],
        supported_provider_keys=sorted(store.SUPPORTED_PROVIDER_KEYS),
        supported_capabilities=sorted(store.SUPPORTED_CAPABILITIES),
        supported_request_protocols=sorted(store.SUPPORTED_REQUEST_PROTOCOLS),
    )


def _filtered_records(q: str | None = None) -> list[dict]:
    rows = store.list_records()
    normalized = str(q or "").strip().casefold()
    if not normalized:
        return rows

    def searchable_text(row: dict) -> str:
        values = [str(row.get(field) or "") for field in _SEARCH_FIELDS]
        # Include the human-facing labels as well as stable provider/capability
        # keys, so an administrator can search with either vocabulary.
        for key in (row.get("provider_key"), row.get("capability")):
            alias = _SEARCH_ALIASES.get(str(key or "").strip().lower())
            if alias:
                values.append(alias)
        return " ".join(values).casefold()

    return [row for row in rows if normalized in searchable_text(row)]


def list_configs(
    q: str | None = None,
    *,
    page: int | None = None,
    size: int | None = None,
) -> ModelAccessConfigBundleResponse:
    """Return model-access records while preserving the legacy bundle API.

    Existing internal callers use ``list_configs()`` and expect the complete
    bundle. Optional arguments are provided for callers that only need a
    filtered page; the HTTP router uses :func:`list_configs_page` to obtain the
    total independently for ``meta_data``.
    """
    rows = _filtered_records(q)
    if page is not None or size is not None:
        current_page = max(0, int(page or 0))
        page_size = max(1, int(size or 100))
        start = current_page * page_size
        rows = rows[start : start + page_size]
    return _bundle(rows)


def list_configs_page(
    *, q: str | None = None, page: int = 0, size: int = 100
) -> tuple[ModelAccessConfigBundleResponse, int]:
    """Return one filtered page and the total number of matching records."""
    rows = _filtered_records(q)
    total = len(rows)
    current_page = max(0, int(page))
    page_size = max(1, int(size))
    start = current_page * page_size
    return _bundle(rows[start : start + page_size]), total


def create(cmd: CreateModelAccessCommand) -> ModelAccessConfigResponse:
    try:
        return _response(store.create_record(**cmd.dict()))
    except (ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


def update(config_id: str, cmd: UpdateModelAccessCommand) -> ModelAccessConfigResponse:
    try:
        return _response(store.update_record(config_id, **cmd.dict(exclude_unset=True)))
    except (ValueError, RuntimeError) as exc:
        raise _error(exc, not_found="不存在" in str(exc)) from exc


def delete(config_id: str) -> None:
    try:
        store.delete_record(config_id)
    except (ValueError, RuntimeError) as exc:
        raise _error(exc, not_found="不存在" in str(exc)) from exc


def _health_candidates(endpoint: str) -> list[str]:
    """Generate service health routes for common ``/api/*/infer`` layouts."""
    raw = str(endpoint or "").strip().rstrip("/")
    if not raw:
        return []
    parsed = urlsplit(raw)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    path = parsed.path.rstrip("/")
    candidates: list[str] = []

    def add(path_value: str) -> None:
        normalized = "/" + path_value.strip("/") if path_value else ""
        candidate = origin + normalized
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    add(path + "/health")
    parts = [part for part in path.split("/") if part]
    if parts:
        parent = parts[:-1]
        add("/".join(parent) + "/health")
        if parent and parent[-1].lower() == "rex":
            add("/".join(parent[:-1]) + "/health")
        add("health")
    if raw not in candidates:
        candidates.append(raw)
    return candidates


def test_connection(config_id: str) -> ModelAccessConfigTestResponse:
    row = store.get_record(config_id)
    if row is None:
        raise _error(ValueError("模型接入配置不存在"), not_found=True)
    endpoint = str(row.get("endpoint") or "")
    headers: dict[str, str] = {}
    started = time.perf_counter()
    ok = False
    reachable = False
    response_status = None
    message = ""
    try:
        is_volcengine = str(row.get("provider_key") or "").strip().lower() == "volcengine"
        if is_volcengine:
            endpoint = store.validate_volcengine_coding_endpoint(endpoint)
        # Validate the billing boundary before materializing credentials or
        # constructing a client, including for legacy records already saved.
        headers.update(store.request_headers(row))
        probe_timeout = min(max(float(row.get("timeout_seconds") or 300), 1.0), 15.0)
        with httpx.Client(timeout=probe_timeout, verify=bool(row.get("verify_tls")), follow_redirects=False) as client:
            if is_volcengine:
                # A model-list probe checks reachability/authentication only;
                # it cannot prove the configured ID accepts image inference.
                probe_url = endpoint.rstrip("/") + "/models"
                response = client.get(probe_url, headers=headers)
                response_status = response.status_code
                reachable = True
                ok = 200 <= response.status_code < 300
                if not ok and response_status in {401, 403}:
                    message = "火山方舟鉴权失败（HTTP 401/403），请检查 API Key"
                else:
                    message = (
                        "火山方舟 Coding Plan 连通性检查通过；未验证所填模型 ID 或图像推理能力"
                        if ok else f"火山方舟 Coding Plan 返回 HTTP {response_status}"
                    )
            else:
                response = None
                for health_url in _health_candidates(endpoint):
                    candidate = client.get(health_url, headers=headers)
                    response = candidate
                    response_status = candidate.status_code
                    reachable = True
                    if 200 <= candidate.status_code < 300:
                        ok = True
                        break
                if not ok:
                    response = client.get(endpoint, headers=headers)
                    response_status = response.status_code
                    reachable = True
                    ok = 200 <= response.status_code < 300
                message = "模型服务连通性检查通过" if ok else f"模型服务返回 HTTP {response_status}"
    except Exception as exc:
        message = f"模型服务不可达：{str(exc)[:500]}"
    elapsed = int((time.perf_counter() - started) * 1000)
    try:
        updated = store.record_check(config_id, status="PASSED" if ok else "FAILED", message=message)
    except (ValueError, RuntimeError) as exc:
        raise _error(exc, not_found="不存在" in str(exc)) from exc
    return ModelAccessConfigTestResponse(
        config=_response(updated), ok=ok, endpoint_reachable=reachable,
        response_status=response_status, latency_ms=elapsed, message=message,
    )
