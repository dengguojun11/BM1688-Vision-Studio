"""Local HTTP model-provider simulator for 7GAI contract verification.

This process is intentionally development-only.  It implements the small
HTTP contract consumed by :class:`HttpProviderAdapter` so a local acceptance
run can exercise real sockets, JSON serialization and response validation
without requiring a model or GPU.

Usage::

    python scripts/ops/provider_http_simulator.py --host 127.0.0.1 --port 18190
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ProviderSimulatorHandler(BaseHTTPRequestHandler):
    server_version = "7GAIProviderSimulator/1.0"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok", "provider": "7gai-local"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") not in {"", "/predict"}:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        image_url = str(body.get("image_url") or "").strip()
        labels = body.get("labels")
        if not image_url or not isinstance(labels, list) or not labels:
            self._write_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "image_url_and_labels_required"},
            )
            return
        label = labels[0]
        if isinstance(label, dict):
            label = label.get("name") or label.get("value") or "object"
        result = {
            "label": str(label),
            "confidence": 0.91,
            "tool": "rectTool",
            "image_url": image_url,
        }
        self._write_json(
            HTTPStatus.OK,
            {
                "model": "7gai-local-provider",
                "provider_version": "1.0.0",
                "results": [result],
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        # Keep acceptance logs concise and free of request payloads.
        return


def create_server(host: str = "127.0.0.1", port: int = 18190) -> ThreadingHTTPServer:
    """Create, but do not start, a simulator server for tests or local runs."""

    return ThreadingHTTPServer((host, int(port)), ProviderSimulatorHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="7GAI local HTTP Provider simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18190)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port)
    print(
        f"7GAI provider simulator listening on http://{args.host}:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
