"""本地 start_labelu_local.py 启动会话：用于开发态强制刷新已打开页面。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from labelu.internal.common.labelu_frontend_integration import REPO_ROOT

ENV_SESSION_ID = "LABELU_LOCAL_DEV_SESSION"
ENV_APP_PORTS = "LABELU_LOCAL_DEV_APP_PORTS"
ENV_FORCE_NO_CACHE = "LABELU_FORCE_NO_CACHE"
DEFAULT_LOCAL_APP_PORTS = (9888,)

RELOAD_SCRIPT_TAG = '<script defer src="/assets/labelu_local_dev_reload.js"></script>'
SESSION_FILE = REPO_ROOT / ".tmp" / "local_runtime" / "dev_session.json"


def local_dev_session_id() -> str:
    return str(os.environ.get(ENV_SESSION_ID) or "").strip()


def local_dev_app_ports() -> tuple[int, ...]:
    raw = str(os.environ.get(ENV_APP_PORTS) or "").strip()
    ports: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ports.append(int(token))
        except ValueError:
            continue
    if ports:
        return tuple(sorted(set(ports)))
    return DEFAULT_LOCAL_APP_PORTS


def is_local_dev_mode() -> bool:
    return bool(local_dev_session_id())


def is_force_no_cache() -> bool:
    if not is_local_dev_mode():
        return False
    return str(os.environ.get(ENV_FORCE_NO_CACHE) or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def is_local_dev_app_origin(host: str | None, port: int | str | None) -> bool:
    hostname = str(host or "").strip().lower()
    if not hostname:
        return False
    try:
        port_value = int(port or 0)
    except (TypeError, ValueError):
        return False
    if port_value not in local_dev_app_ports():
        return False
    if hostname in {"127.0.0.1", "localhost", "::1"}:
        return True
    if re.match(r"^192\.168\.\d+\.\d+$", hostname):
        return True
    if re.match(r"^10\.\d+\.\d+\.\d+$", hostname):
        return True
    manual = str(os.environ.get("LABELU_PUBLIC_HOST") or "").strip().lower()
    return bool(manual and hostname == manual)


def no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "max-age=0, no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def dev_session_headers() -> dict[str, str]:
    headers = dict(no_cache_headers())
    session_id = local_dev_session_id()
    if session_id:
        headers["X-LabelU-Dev-Session"] = session_id
    return headers


def write_dev_session_file(
    *,
    session_id: str,
    app_port: int,
    vite_port: int,
    public_host: str,
) -> Path:
    payload = {
        "session_id": session_id,
        "app_port": int(app_port),
        "vite_port": int(vite_port),
        "public_host": str(public_host or "127.0.0.1"),
        "repo_root": str(REPO_ROOT),
    }
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return SESSION_FILE


def read_dev_session_payload() -> dict[str, Any] | None:
    session_id = local_dev_session_id()
    if not session_id:
        return None
    payload: dict[str, Any] = {
        "session_id": session_id,
        "app_ports": list(local_dev_app_ports()),
    }
    if SESSION_FILE.is_file():
        try:
            raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload.update(raw)
        except (OSError, json.JSONDecodeError):
            pass
    payload["session_id"] = session_id
    payload["app_ports"] = list(local_dev_app_ports())
    return payload


def local_dev_ports_meta_tag() -> str:
    ports = ",".join(str(port) for port in local_dev_app_ports())
    return f'<meta name="labelu-local-dev-ports" content="{ports}" />'


def maybe_inject_local_dev_reload(html: str) -> str:
    if not is_local_dev_mode():
        return html
    marker = "</head>"
    if marker not in html:
        return html
    inject = []
    meta_tag = local_dev_ports_meta_tag()
    if meta_tag not in html:
        inject.append(meta_tag)
    if "labelu_local_dev_reload.js" not in html:
        inject.append(RELOAD_SCRIPT_TAG)
    if not inject:
        return html
    return html.replace(marker, "\n".join(inject) + "\n" + marker, 1)
