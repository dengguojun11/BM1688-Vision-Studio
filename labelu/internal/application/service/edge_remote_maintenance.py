from __future__ import annotations

import threading
from typing import Any

from loguru import logger

from labelu.internal.application.service import edge_ssh_validation
from labelu.internal.common import edge_device_registry
from labelu.internal.common.config import settings
from labelu.internal.common.edge_ssh_client import ssh_connect

_WATCHDOG_LOCK = threading.Lock()
_WATCHDOG_STARTED = False


def cleanup_stale_remote_edge_jobs() -> dict[str, Any]:
    retention_hours = int(
        getattr(settings, "EDGE_REMOTE_FAILED_JOB_RETENTION_HOURS", 24) or 24
    )
    results: list[dict[str, Any]] = []
    for record in edge_device_registry.list_device_records():
        if not isinstance(record, dict) or not bool(record.get("enabled", True)):
            continue
        device_id = str(record.get("id") or "").strip()
        cfg = edge_device_registry.load_device_config(device_id)
        if not (
            str(cfg.get("host") or "").strip()
            and str(cfg.get("user") or "").strip()
            and (
                str(cfg.get("password") or "")
                or str(cfg.get("private_key_path") or "")
            )
        ):
            continue
        client = None
        try:
            client = ssh_connect(
                host=str(cfg.get("host") or ""),
                port=int(cfg.get("port") or 22),
                username=str(cfg.get("user") or ""),
                password=str(cfg.get("password") or ""),
                private_key_path=str(cfg.get("private_key_path") or "") or None,
                timeout=int(cfg.get("connect_timeout_sec") or 30),
            )
            result = edge_ssh_validation.cleanup_stale_remote_job_dirs(
                client,
                remote_base=str(cfg.get("remote_base") or "/data/labelu_edge/jobs"),
                retention_hours=retention_hours,
            )
            result.update(device_id=device_id, host=str(cfg.get("host") or ""))
            results.append(result)
        except Exception as exc:
            logger.warning(
                "edge remote maintenance failed device_id={} host={} err={}",
                device_id,
                cfg.get("host"),
                exc,
            )
            results.append(
                {
                    "status": "failed",
                    "device_id": device_id,
                    "host": str(cfg.get("host") or ""),
                    "removed": [],
                    "message": str(exc)[:800],
                }
            )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
    return {
        "retention_hours": retention_hours,
        "devices": results,
        "removed_count": sum(len(item.get("removed") or []) for item in results),
    }


def schedule_edge_remote_maintenance_watchdog() -> None:
    global _WATCHDOG_STARTED
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    interval = max(
        300,
        int(getattr(settings, "EDGE_REMOTE_CLEANUP_INTERVAL_SECONDS", 1800) or 1800),
    )

    def _tick() -> None:
        try:
            summary = cleanup_stale_remote_edge_jobs()
            logger.info(
                "edge remote maintenance finished devices={} removed={} retention={}h",
                len(summary.get("devices") or []),
                summary.get("removed_count", 0),
                summary.get("retention_hours"),
            )
        except Exception:
            logger.exception("edge remote maintenance watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    first_run = threading.Timer(60.0, _tick)
    first_run.daemon = True
    first_run.start()
    logger.info(
        "edge remote maintenance watchdog started interval={}s retention={}h",
        interval,
        int(getattr(settings, "EDGE_REMOTE_FAILED_JOB_RETENTION_HOURS", 24) or 24),
    )
