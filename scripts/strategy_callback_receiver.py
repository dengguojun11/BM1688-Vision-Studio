"""Standalone receiver for LabelU strategy-evolution callbacks.

This service intentionally has no user authentication. It is a local or
controlled-network integration tool for inspecting callback payloads.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


DEFAULT_CALLBACK_PATH = "/api/v1/strategy-evolution/platform-callbacks"
DEFAULT_STORAGE_DIR = ".tmp/strategy-callback-receiver"
MAX_RECENT_CALLBACKS = 100
CALLBACK_HEADERS = (
    "X-LabelU-Timestamp",
    "X-LabelU-Event-Id",
    "X-LabelU-Signature",
)


class CallbackStore:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path
        self._items: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_CALLBACKS)
        self._lock = Lock()

    def append(self, item: dict[str, Any]) -> None:
        with self._lock:
            self._items.append(item)
            if self.storage_path is None:
                return
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")

    def recent(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._items)[-limit:]

    def count(self) -> int:
        with self._lock:
            return len(self._items)


def _storage_path() -> Path:
    configured = str(os.getenv("STRATEGY_CALLBACK_RECEIVER_STORAGE", "")).strip()
    if configured:
        return Path(configured)
    return Path(DEFAULT_STORAGE_DIR) / "callbacks.jsonl"


app = FastAPI(
    title="LabelU Strategy Callback Receiver",
    version="1.0.0",
)
store = CallbackStore(_storage_path())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "strategy_callback_receiver",
        "received_count": store.count(),
    }


@app.post(DEFAULT_CALLBACK_PATH)
async def receive_strategy_callback(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": f"invalid JSON: {exc}"},
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "callback body must be a JSON object"},
        )

    received_at = datetime.now(timezone.utc).isoformat()
    callback_headers = {
        name: request.headers.get(name)
        for name in CALLBACK_HEADERS
        if request.headers.get(name)
    }
    store.append(
        {
            "received_at": received_at,
            "headers": callback_headers,
            "payload": payload,
        }
    )
    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "message": "ok",
            "received": True,
            "callback_event_id": payload.get("callback_event_id"),
        },
    )


@app.get("/api/v1/strategy-evolution/platform-callbacks/recent")
async def recent_strategy_callbacks(limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_RECENT_CALLBACKS))
    items = store.recent(limit)
    return {"data": items, "total": len(items)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9898, type=int)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
