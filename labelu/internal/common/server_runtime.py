"""按实际监听地址统一 HOST/PORT/MEDIA_HOST，并可选写回 .env。"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from labelu.internal.common.config import settings

_LOCK = threading.Lock()
_STARTUP_FINALIZED = False

_MEDIA_HOST_KEY = "MEDIA_HOST"


def resolve_display_host(host: str) -> str:
    if host in ("0.0.0.0", "::", "[::]", "[::1]"):
        return "127.0.0.1"
    return host


def resolve_media_host(
    host: str,
    port: int | str,
    media_host_override: Optional[str] = None,
) -> str:
    if (media_host_override or "").strip():
        return media_host_override.strip().rstrip("/")
    display = resolve_display_host(host)
    return f"{settings.SCHEME}://{display}:{int(port)}"


def apply_listen_settings(
    host: str,
    port: int | str,
    media_host_override: Optional[str] = None,
) -> str:
    """用 CLI / 启动参数覆盖 .env 中的 HOST、PORT、MEDIA_HOST。"""
    port_str = str(int(port))
    media_host = resolve_media_host(host, port_str, media_host_override)
    object.__setattr__(settings, "HOST", host)
    object.__setattr__(settings, "PORT", port_str)
    object.__setattr__(settings, "MEDIA_HOST", media_host)
    return media_host


def _dotenv_path() -> Path:
    return Path.cwd() / ".env"


def _replace_or_append_dotenv_key(path: Path, key: str, value: str) -> None:
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=).*$", re.IGNORECASE)
    lines: list[str] = []
    replaced = False
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if pattern.match(line.strip()):
                lines[i] = f"{key}={value}"
                replaced = True
                break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"# 与 labelu 启动参数同步（MEDIA_HOST_AUTO_SYNC）")
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sync_dotenv_media_host(media_host: str) -> None:
    """将解析后的 MEDIA_HOST 写回当前目录 .env，避免下次启动仍用旧端口。"""
    if not settings.MEDIA_HOST_AUTO_SYNC:
        return
    target = media_host.strip().rstrip("/")
    if not target:
        return
    dotenv = _dotenv_path()
    if dotenv.is_file():
        existing = _read_dotenv_value(dotenv, _MEDIA_HOST_KEY)
        if existing and existing.rstrip("/") == target:
            return
    try:
        if dotenv.is_file():
            _replace_or_append_dotenv_key(dotenv, _MEDIA_HOST_KEY, target)
        else:
            dotenv.write_text(
                f"# LabelU 媒体根 URL（与启动端口同步）\n{_MEDIA_HOST_KEY}={target}\n",
                encoding="utf-8",
            )
        logger.info(
            "已将 {} 同步为 {}（与本次启动 --host/--port 一致）",
            _MEDIA_HOST_KEY,
            target,
        )
    except OSError as exc:
        logger.warning("无法将 MEDIA_HOST 写入 .env：{}", exc)


def _read_dotenv_value(path: Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)\s*$", re.IGNORECASE)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def finalize_server_startup(*, jwt_from_env: bool) -> None:
    """
    在 HOST/PORT/MEDIA_HOST 已按启动参数设置后调用一次：
    图像流水线 .env 补全、启动诊断日志、可选依赖预热。
    """
    global _STARTUP_FINALIZED
    with _LOCK:
        if _STARTUP_FINALIZED:
            return
        from labelu.internal.common.image_pipeline.env_bootstrap import (
            apply_image_pipeline_env_bootstrap,
        )
        from labelu.internal.common.boot_diagnostics import log_boot_profile
        from labelu.internal.common.production_security import enforce_production_security
        from labelu.internal.common.optional_dependencies import (
            warm_image_pipeline_dependencies_at_startup,
            warm_yolo_dependencies_at_startup,
        )

        apply_image_pipeline_env_bootstrap()
        # Run before optional dependency warm-up so an unsafe production
        # process fails fast without starting background workers.
        enforce_production_security(jwt_from_env=jwt_from_env)
        log_boot_profile(jwt_from_env=jwt_from_env)
        warm_yolo_dependencies_at_startup()
        warm_image_pipeline_dependencies_at_startup()
        _STARTUP_FINALIZED = True


def configure_server_from_cli(
    host: str,
    port: int,
    media_host: Optional[str] = None,
    *,
    jwt_from_env: bool,
) -> str:
    """labelu 默认子命令：统一运行时与 .env 中的 MEDIA_HOST，并输出启动诊断。"""
    media = apply_listen_settings(host, port, media_host)
    sync_dotenv_media_host(media)
    parsed = urlparse(media)
    logger.info(
        "服务监听 {}:{}，MEDIA_HOST={}（附件绝对链接与浏览器访问需一致）",
        host,
        port,
        media if parsed.netloc else media,
    )
    finalize_server_startup(jwt_from_env=jwt_from_env)
    return media
