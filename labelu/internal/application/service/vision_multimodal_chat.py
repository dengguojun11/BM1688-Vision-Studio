"""OpenAI 兼容 Chat Completions：多图 + 文本，返回助手原文（不作 JSON 解析）。"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from labelu.internal.common.config import settings
from labelu.internal.common.model_access_config import (
    request_headers,
    resolve_model_integration,
)


def _chat_url(base: str) -> str:
    base = (base or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "output_text":
                    parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return str(content)


def chat_multimodal_markdown(
    *,
    base_url: str,
    api_key: str,
    model: str,
    user_text: str,
    images_jpeg: list[bytes],
    max_tokens: int = 8192,
    temperature: float = 0.25,
    timeout: int | None = None,
    max_retries: int = 0,
    system_prompt: str | None = None,
    verify_tls: bool | None = None,
) -> str:
    """同步调用；返回助手文本（通常为 Markdown）。"""
    url = _chat_url(base_url)
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for jb in images_jpeg:
        b64 = base64.standard_b64encode(jb).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )
    messages: list[dict[str, Any]] = []
    if (system_prompt or "").strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": content})
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Prefer the system-management connection when the endpoint belongs to
    # the configured visual model.  This supports services without auth and
    # custom API-key headers, while retaining the legacy Bearer behaviour for
    # AI-labeling profiles and .env-only installations.
    configured = resolve_model_integration("vision_llm")
    configured_endpoint = (
        str(configured.get("endpoint") or "").rstrip("/") if configured else ""
    )
    requested_endpoint = str(base_url or "").rstrip("/")
    if configured and configured_endpoint == requested_endpoint:
        headers = request_headers(configured)
        if timeout is None:
            timeout = int(
                configured.get("timeout_seconds") or settings.AI_MODEL_TIMEOUT_SECONDS
            )
        if verify_tls is None:
            verify_tls = bool(configured.get("verify_tls", False))
    else:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    headers["Content-Type"] = "application/json"
    to = timeout if timeout is not None else settings.AI_MODEL_TIMEOUT_SECONDS
    retries = max(0, int(max_retries))
    with httpx.Client(
        timeout=to, verify=True if verify_tls is None else bool(verify_tls)
    ) as client:
        for attempt in range(retries + 1):
            try:
                r = client.post(url, headers=headers, json=body)
                r.raise_for_status()
                chat = r.json()
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                transient = isinstance(exc, httpx.TransportError) or (
                    exc.response.status_code == 408
                    or exc.response.status_code == 429
                    or exc.response.status_code >= 500
                )
                if not transient or attempt >= retries:
                    raise
                time.sleep(min(2**attempt, 2))
    choice0 = (chat.get("choices") or [{}])[0]
    msg = choice0.get("message") or {}
    return _message_content_text(msg.get("content")).strip()
