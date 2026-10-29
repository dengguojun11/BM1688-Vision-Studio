from __future__ import annotations

import httpx
import pytest

from labelu.internal.application.service import vision_multimodal_chat as svc


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "# 已完成"}}]}


class _Client:
    captured = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, *, headers, json):
        self.captured.update({"url": url, "headers": headers, "body": json})
        return _Response()


def test_chat_uses_system_connection_auth_tls_and_timeout(monkeypatch):
    _Client.captured = {}
    monkeypatch.setattr(
        svc,
        "resolve_model_integration",
        lambda key: (
            {
                "endpoint": "http://model.example/v1",
                "auth_type": "api_key",
                "auth_header": "X-Service-Key",
                "api_key_encrypted": "configured",
                "api_key_masked": "****ured",
                "timeout_seconds": 47,
                "verify_tls": False,
            }
            if key == "vision_llm"
            else None
        ),
    )
    monkeypatch.setattr(
        svc, "request_headers", lambda _row: {"X-Service-Key": "secret"}
    )
    monkeypatch.setattr(svc.httpx, "Client", _Client)

    result = svc.chat_multimodal_markdown(
        base_url="http://model.example/v1",
        api_key="legacy-key",
        model="vision-model",
        user_text="请输出报告",
        images_jpeg=[b"jpeg"],
    )

    assert result == "# 已完成"
    assert _Client.captured["url"] == "http://model.example/v1/chat/completions"
    assert _Client.captured["headers"] == {
        "X-Service-Key": "secret",
        "Content-Type": "application/json",
    }
    assert _Client.captured["body"]["model"] == "vision-model"


class _RetryClient:
    calls = 0
    failures = []
    kwargs = {}

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, _url, *, headers, json):
        type(self).calls += 1
        if type(self).calls <= len(type(self).failures):
            raise type(self).failures[type(self).calls - 1]
        return _Response()


def _configure_retry_client(monkeypatch, failures):
    _RetryClient.calls = 0
    _RetryClient.failures = failures
    _RetryClient.kwargs = {}
    monkeypatch.setattr(svc, "resolve_model_integration", lambda _key: None)
    monkeypatch.setattr(svc.httpx, "Client", _RetryClient)
    monkeypatch.setattr(svc.time, "sleep", lambda _seconds: None)


def test_chat_retries_one_transient_timeout_with_explicit_timeout(monkeypatch):
    _configure_retry_client(monkeypatch, [httpx.ReadTimeout("read timed out")])

    result = svc.chat_multimodal_markdown(
        base_url="http://model.example/v1",
        api_key="legacy-key",
        model="vision-model",
        user_text="请输出报告",
        images_jpeg=[],
        timeout=600,
        max_retries=1,
    )

    assert result == "# 已完成"
    assert _RetryClient.calls == 2
    assert _RetryClient.kwargs["timeout"] == 600


def test_chat_does_not_retry_permanent_client_error(monkeypatch):
    request = httpx.Request("POST", "http://model.example/v1/chat/completions")
    response = httpx.Response(400, request=request)
    error = httpx.HTTPStatusError("bad request", request=request, response=response)
    _configure_retry_client(monkeypatch, [error])

    with pytest.raises(httpx.HTTPStatusError):
        svc.chat_multimodal_markdown(
            base_url="http://model.example/v1",
            api_key="legacy-key",
            model="vision-model",
            user_text="请输出报告",
            images_jpeg=[],
            max_retries=1,
        )

    assert _RetryClient.calls == 1
