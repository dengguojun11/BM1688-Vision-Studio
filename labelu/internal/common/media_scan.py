"""Small, provider-neutral media malware scanning adapter.

The scanner is deliberately invoked without a shell.  A deployment can use
ClamAV or another executable by supplying a command template containing the
``{path}`` placeholder.  The upload service persists the result so a later
dataset export never has to infer whether an asset was scanned.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from labelu.internal.common.config import settings


@dataclass(frozen=True)
class MediaScanResult:
    status: str
    provider: str
    error: str | None = None
    scanned_at: datetime | None = None
    signature: str | None = None


def scan_media_path(path: Path) -> MediaScanResult:
    """Scan ``path`` and return a stable status without raising provider errors."""
    now = datetime.now(timezone.utc)
    if not settings.MEDIA_VIRUS_SCAN_ENABLED:
        return MediaScanResult("NOT_CONFIGURED", "disabled", scanned_at=now)

    template = (settings.MEDIA_VIRUS_SCAN_COMMAND or "").strip()
    if not template:
        return MediaScanResult("ERROR", "configured", "扫描命令未配置", now)

    command_text = template.replace("{path}", str(path))
    try:
        command = shlex.split(command_text, posix=False)
        if "{path}" not in template:
            command.append(str(path))
        if not command:
            return MediaScanResult("ERROR", "configured", "扫描命令为空", now)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.MEDIA_VIRUS_SCAN_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError as exc:
        return MediaScanResult("ERROR", "configured", f"扫描程序不存在: {exc}", now)
    except subprocess.TimeoutExpired:
        return MediaScanResult("ERROR", "configured", "病毒扫描超时", now)
    except (OSError, ValueError) as exc:
        return MediaScanResult("ERROR", "configured", f"病毒扫描执行失败: {exc}", now)

    if completed.returncode == 0:
        return MediaScanResult("CLEAN", "configured", scanned_at=now)
    if completed.returncode == 1:
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        signature = None
        for line in output.splitlines():
            if "FOUND" in line:
                signature = line.rsplit(":", 1)[-1].replace("FOUND", "").strip()
                break
        return MediaScanResult(
            "INFECTED",
            "configured",
            "扫描器报告文件感染",
            now,
            signature or "未知威胁",
        )
    detail = (completed.stderr or completed.stdout or "扫描器返回失败状态").strip()
    return MediaScanResult("ERROR", "configured", detail[:1000], now)
