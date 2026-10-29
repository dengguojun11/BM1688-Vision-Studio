import asyncio

import httpx

from labelu.internal.middleware import frontend_dev_proxy


def test_request_vite_with_retry_retries_remote_protocol_error(monkeypatch):
    calls = []

    class FirstClient:
        async def request(self, method, target, headers=None):
            calls.append(("primary", method, target, dict(headers or {})))
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    class RetryClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, target, headers=None):
            calls.append(("retry", method, target, dict(headers or {})))
            return httpx.Response(200, text="ok")

    monkeypatch.setattr(frontend_dev_proxy, "_get_http_client", lambda: FirstClient())
    monkeypatch.setattr(frontend_dev_proxy.httpx, "AsyncClient", RetryClient)

    resp = asyncio.run(
        frontend_dev_proxy._request_vite_with_retry(
            "GET",
            "http://127.0.0.1:3004/src/pages/tasks.[id].edit/style.ts",
            {"host": "127.0.0.1:3004"},
        )
    )

    assert resp.status_code == 200
    assert calls[0][0] == "primary"
    assert calls[1][0] == "retry"
    assert calls[1][3]["connection"] == "close"
