"""Dedicated worker runtime for automatic sample preannotation.

The API only creates durable ``AutoLabelJob`` rows when the switch is enabled.
This module is intentionally small so it can run under systemd without a web
event loop.  Claiming is atomic and fenced in the database; a restarted
worker reclaims expired jobs and an old process can no longer write progress.
"""

from __future__ import annotations

import argparse
import os
import time
import uuid

from loguru import logger

from labelu.internal.application.service import auto_sample_preannotate
from labelu.internal.common.config import settings


_LOG = "[auto-preannotate-worker]"


def dedicated_worker_enabled() -> bool:
    return bool(getattr(settings, "AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False))


def worker_owner() -> str:
    return f"auto-preannotate:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def run_worker_once(*, limit: int | None = None) -> int:
    """Run at most ``limit`` jobs synchronously and return completed claims."""
    if not dedicated_worker_enabled():
        logger.warning("{} disabled; refusing to execute jobs", _LOG)
        return 0
    # One model process at a time is the safe default for a shared GPU/CPU.
    # Operators can raise this only after measuring memory and inference P95.
    configured_limit = int(
        limit
        if limit is not None
        else getattr(settings, "AUTO_PREANNOTATE_WORKER_BATCH_LIMIT", 1)
    )
    configured_limit = max(1, configured_limit)
    max_concurrent = max(
        1, int(getattr(settings, "AUTO_PREANNOTATE_MAX_CONCURRENT_JOBS", 1))
    )
    configured_limit = min(configured_limit, max_concurrent)

    count = 0
    # The service performs the atomic DB claim.  Calling it one row at a time
    # avoids in-process fan-out and keeps the memory bound deterministic.
    for _ in range(configured_limit):
        from labelu.internal.common.db import session_scope
        from labelu.internal.adapter.persistence import crud_auto_label_job

        with session_scope() as db:
            rows = crud_auto_label_job.list_runnable_auto_sample_jobs(db, limit=1)
            job_id = int(rows[0].id) if rows else None
        if job_id is None:
            break
        count += 1
        auto_sample_preannotate._run_durable_auto_preannotate_job(
            job_id,
            lease_seconds=int(
                getattr(settings, "AUTO_PREANNOTATE_WORKER_LEASE_SECONDS", 900)
            ),
        )
    return count


def run_worker_loop(*, once: bool = False) -> None:
    if not dedicated_worker_enabled():
        raise RuntimeError(
            "AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED=false，拒绝启动预标注 Worker"
        )
    interval = max(
        1.0,
        float(getattr(settings, "AUTO_PREANNOTATE_WORKER_INTERVAL_SECONDS", 5.0)),
    )
    while True:
        run_worker_once()
        if once:
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="自动样本预标注独立 Worker")
    parser.add_argument("--once", action="store_true", help="只执行一次扫描")
    args = parser.parse_args()
    run_worker_loop(once=bool(args.once))


__all__ = ["dedicated_worker_enabled", "run_worker_once", "run_worker_loop"]

