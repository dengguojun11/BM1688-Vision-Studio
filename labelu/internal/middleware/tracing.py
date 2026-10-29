import time

from loguru import logger
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from labelu.internal.common.business_log import (
    new_request_id,
    peek_user_id_from_authorization,
    set_request_context,
    should_log_http_payload,
    summarize_for_log,
    summarize_http_payload,
)
from labelu.internal.common.config import settings
from labelu.internal.common.third_party_call_audit import (
    classify_third_party_call,
    persist_third_party_call,
)

QUIET_HTTP_TRACE_PATHS = {"/api/v1/local-dev/session"}


def should_skip_http_trace(path: str) -> bool:
    return path in QUIET_HTTP_TRACE_PATHS


def _format_query(request: Request) -> str:
    if not request.query_params:
        return ""
    return summarize_for_log(dict(request.query_params), max_len=400)


def _http_trace_line(
    *,
    phase: str,
    method: str,
    path: str,
    request_id: str,
    user_id: str | None = None,
    query_summary: str = "",
    body_summary: str = "",
    status_code: str | int = "",
    elapsed_ms: int | None = None,
) -> str:
    """拼 HTTP 追踪正文；通过 logger.info('{}', line) 保留日志格式及 JSON 字面量。"""
    if phase == "request":
        parts = [
            f"[http] {method} {path} request",
            f"rid={request_id}",
            f"uid={user_id if user_id is not None else '-'}",
        ]
        if query_summary and method in {"GET", "HEAD", "DELETE"}:
            parts.append(f"query={query_summary}")
        if body_summary:
            parts.append(f"body={body_summary}")
        return " ".join(parts)
    parts = [
        f"[http] {method} {path} response",
        f"rid={request_id}",
        f"status={status_code}",
    ]
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms}")
    if body_summary:
        parts.append(f"body={body_summary}")
    return " ".join(parts)


async def _read_request_body(request: Request) -> tuple[Request, bytes]:
    raw_body = await request.body()

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(request.scope, receive), raw_body


async def _capture_response_payload(response: Response) -> tuple[Response, str]:
    content_type = response.headers.get("content-type", "")
    body_chunks = [chunk async for chunk in response.body_iterator]
    body_bytes = b"".join(body_chunks)
    summary = summarize_http_payload(body_bytes, content_type=content_type)
    response.body_iterator = iterate_in_threadpool(iter(body_chunks))
    return response, summary


class TracingMiddleWare(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        scope = request.scope
        path = scope.get("path", "")
        if should_skip_http_trace(path):
            return await call_next(request)

        method = request.method
        start = time.perf_counter()
        request_id = new_request_id()
        user_id = peek_user_id_from_authorization(
            request.headers.get("Authorization", "")
        )
        set_request_context(request_id=request_id, user_id=user_id)
        audit_target = classify_third_party_call(
            path=path,
            method=method,
            authorization=request.headers.get("Authorization", ""),
        )

        log_payload = should_log_http_payload(path)
        request_payload_log = ""
        audit_raw_body = b""
        query_summary = (
            _format_query(request) if settings.LOG_HTTP_DETAIL or log_payload else ""
        )

        if log_payload or audit_target:
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                request, raw_body = await _read_request_body(request)
                if audit_target:
                    audit_raw_body = raw_body
                if log_payload:
                    if len(raw_body) > settings.LOG_HTTP_BODY_MAX_CAPTURE_BYTES:
                        request_payload_log = f"<body len={len(raw_body)} omitted>"
                    else:
                        request_payload_log = summarize_http_payload(
                            raw_body,
                            content_type=request.headers.get("content-type", ""),
                        )
            elif log_payload and query_summary:
                request_payload_log = f"<query> {query_summary}"

        with logger.contextualize(request_id=request_id, user_id=user_id or "-"):
            if settings.LOG_HTTP_DETAIL or log_payload:
                logger.info(
                    "{}",
                    _http_trace_line(
                        phase="request",
                        method=method,
                        path=path,
                        request_id=request_id,
                        user_id=user_id,
                        query_summary=query_summary,
                        body_summary=request_payload_log,
                    )
                )
            else:
                logger.info("{} {} start", method, path)

            response = None
            response_payload_log = ""
            audit_error = ""
            try:
                response = await call_next(request)
                capture_audit_error = bool(
                    audit_target
                    and response is not None
                    and response.status_code >= 400
                )
                if (log_payload or capture_audit_error) and response is not None:
                    response, response_payload_log = await _capture_response_payload(
                        response
                    )
                return response
            except Exception as exc:
                audit_error = str(exc)
                raise
            finally:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                status_code = response.status_code if response else "error"
                level = "info"
                if isinstance(status_code, int) and status_code >= 500:
                    level = "error"
                elif isinstance(status_code, int) and status_code >= 400:
                    level = "warning"

                if settings.LOG_HTTP_DETAIL or log_payload:
                    getattr(logger, level)(
                        "{}",
                        _http_trace_line(
                            phase="response",
                            method=method,
                            path=path,
                            request_id=request_id,
                            status_code=status_code,
                            elapsed_ms=elapsed_ms,
                            body_summary=response_payload_log,
                        )
                    )
                else:
                    getattr(logger, level)(
                        "{} {} end status={} elapsed_ms={}",
                        method,
                        path,
                        status_code,
                        elapsed_ms,
                    )
                if audit_target:
                    numeric_status = (
                        int(status_code) if isinstance(status_code, int) else 500
                    )
                    await run_in_threadpool(
                        persist_third_party_call,
                        target=audit_target,
                        request_id=request_id,
                        method=method,
                        path=path,
                        raw_body=audit_raw_body,
                        response_status=numeric_status,
                        duration_ms=elapsed_ms,
                        client_ip=(request.client.host if request.client else None),
                        user_agent=request.headers.get("user-agent"),
                        error_summary=audit_error or response_payload_log or None,
                    )
