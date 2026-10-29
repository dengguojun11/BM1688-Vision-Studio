"""开发态：将主站 HTTP/WS 反向代理到 Vite，保留 API / 自研扩展页在后端。"""

from __future__ import annotations

import asyncio
import os

import httpx
import websockets
from loguru import logger
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from labelu.internal.common.labelu_frontend_integration import (
    get_vite_port,
    should_proxy_to_vite,
)
from labelu.internal.common.local_dev_session import (
    is_local_dev_mode,
    maybe_inject_local_dev_reload,
)

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
    return _http_client


async def _request_vite_with_retry(
    method: str,
    target: str,
    headers: dict[str, str],
) -> httpx.Response:
    try:
        return await _get_http_client().request(method, target, headers=headers)
    except (httpx.RemoteProtocolError, httpx.ReadError):
        retry_headers = dict(headers)
        retry_headers["connection"] = "close"
        async with httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ) as client:
            return await client.request(method, target, headers=retry_headers)


def should_proxy_websocket(path: str, query: str = "") -> bool:
    if path.startswith("/ws"):
        return False
    if path.startswith("/@vite") or path.startswith("/__vite"):
        return True
    if path == "/" and "token=" in query:
        return True
    return False


class FrontendDevProxyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if os.environ.get("LABELU_VITE_ENABLED", "") != "1":
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            path = scope.get("path", "")
            query = scope.get("query_string", b"").decode()
            if should_proxy_websocket(path, query):
                await self._proxy_websocket(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        if request.method not in ("GET", "HEAD") or not should_proxy_to_vite(path):
            await self.app(scope, receive, send)
            return

        vite_port = get_vite_port()
        target = f"http://127.0.0.1:{vite_port}{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"

        forward_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        forward_headers["host"] = f"127.0.0.1:{vite_port}"

        try:
            upstream = await _request_vite_with_retry(
                request.method,
                target,
                forward_headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("Vite 代理失败 {}: {}", target, exc)
            await self.app(scope, receive, send)
            return

        excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in excluded
        }
        content = upstream.content
        media_type = upstream.headers.get("content-type")
        if (
            request.method == "GET"
            and is_local_dev_mode()
            and media_type
            and "text/html" in media_type.lower()
        ):
            try:
                html = content.decode("utf-8")
                content = maybe_inject_local_dev_reload(html).encode("utf-8")
            except UnicodeDecodeError:
                pass

        if request.method == "HEAD":
            resp = Response(status_code=upstream.status_code, headers=headers)
        else:
            resp = Response(
                content=content,
                status_code=upstream.status_code,
                headers=headers,
                media_type=media_type,
            )
        await resp(scope, receive, send)

    async def _proxy_websocket(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        path = scope.get("path", "/")
        query = scope.get("query_string", b"").decode()
        vite_port = get_vite_port()
        upstream_uri = f"ws://127.0.0.1:{vite_port}{path}"
        if query:
            upstream_uri += f"?{query}"

        subprotocols = scope.get("subprotocols") or []

        try:
            async with websockets.connect(
                upstream_uri,
                subprotocols=subprotocols if subprotocols else None,
                max_size=None,
                ping_interval=None,
            ) as upstream:
                await send(
                    {
                        "type": "websocket.accept",
                        "subprotocol": upstream.subprotocol,
                    }
                )

                async def client_to_upstream() -> None:
                    while True:
                        message = await receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            break
                        if message["type"] == "websocket.receive":
                            data = message.get("bytes")
                            if data is not None:
                                await upstream.send(data)
                            else:
                                await upstream.send(message["text"])

                async def upstream_to_client() -> None:
                    async for data in upstream:
                        if isinstance(data, bytes):
                            await send({"type": "websocket.send", "bytes": data})
                        else:
                            await send({"type": "websocket.send", "text": data})

                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(client_to_upstream()),
                        asyncio.create_task(upstream_to_client()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
        except Exception as exc:
            logger.warning("Vite WebSocket 代理失败 {}: {}", upstream_uri, exc)
            await send({"type": "websocket.close", "code": 1011})
