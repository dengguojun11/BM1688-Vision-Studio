"""本地开发启动：为 LabelU 响应附加 no-store 与 dev session 头。"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from labelu.internal.common.local_dev_session import dev_session_headers, is_local_dev_mode


class LocalDevCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_local_dev_mode():
            await self.app(scope, receive, send)
            return

        headers = dev_session_headers()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                existing = {k.decode("latin-1").lower() for k, _ in raw_headers}
                for key, value in headers.items():
                    lower = key.lower()
                    if lower in existing:
                        continue
                    raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
                message = {**message, "headers": raw_headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
