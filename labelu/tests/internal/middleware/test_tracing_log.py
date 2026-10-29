"""HTTP 追踪日志：JSON 响应体不得触发 Loguru format_map KeyError。"""
import asyncio
import json
import os
import re

from loguru import logger
import pytest
from starlette.requests import Request
from starlette.responses import Response

from labelu.internal.common.logger import _format_file_log_record, format_record
from labelu.internal.middleware import tracing
from labelu.internal.middleware.tracing import _http_trace_line, should_skip_http_trace


def test_http_trace_line_includes_json_body():
    body = json.dumps({"data": None})
    line = _http_trace_line(
        phase="response",
        method="GET",
        path="/api/v1/board-agent/jobs/next",
        request_id="abc123",
        status_code=200,
        elapsed_ms=7,
        body_summary=body,
    )
    assert '{"data": null}' in line
    assert "status=200" in line


def test_loguru_formatted_info_accepts_json_braces():
    body = json.dumps({"data": None})
    line = _http_trace_line(
        phase="response",
        method="GET",
        path="/api/v1/x",
        request_id="rid1",
        status_code=200,
        elapsed_ms=1,
        body_summary=body,
    )
    # 旧写法会把 body 嵌入 format 串，sink 二次解析会 KeyError: '"data"'
    logger.info("{}", line)


def test_local_dev_session_skips_http_trace():
    assert should_skip_http_trace("/api/v1/local-dev/session") is True
    assert should_skip_http_trace("/api/v1/tasks") is False


@pytest.mark.parametrize("log_format", [_format_file_log_record, format_record])
def test_http_request_and_response_keep_log_prefix_and_literal_payload(
    monkeypatch, log_format
):
    lines = []
    body = '{"data": {"html": "<div>{value}</div>"}}'
    monkeypatch.setattr(tracing.settings, "LOG_HTTP_DETAIL", True)
    monkeypatch.setattr(tracing, "should_log_http_payload", lambda _: True)
    monkeypatch.setattr(tracing, "classify_third_party_call", lambda **_: None)
    monkeypatch.setattr(tracing, "_format_query", lambda _: body)

    async def capture(response):
        return response, body

    async def call_next(request):
        return Response(status_code=200)

    monkeypatch.setattr(tracing, "_capture_response_payload", capture)
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/v1/test", "headers": []}
    )
    middleware = tracing.TracingMiddleWare(lambda *_: None)
    sink_id = logger.add(lines.append, format=log_format, catch=False)
    try:
        asyncio.run(middleware.dispatch(request, call_next))
    finally:
        logger.remove(sink_id)

    trace_lines = [str(line).strip() for line in lines if "[http]" in str(line)]
    assert len(trace_lines) == 2
    for line in trace_lines:
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \|", line)
        assert f"pid={os.getpid()}" in line
        assert body in line
