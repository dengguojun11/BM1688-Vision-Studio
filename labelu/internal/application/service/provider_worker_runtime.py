"""Standalone runtime for the 7GAI Provider job queue.

The HTTP API only persists Provider jobs.  This module supplies the small
process boundary that claims and executes those jobs through the existing
``provider_worker.run_next_provider_job`` state machine.  It deliberately
does not register adapters: runtime adapter registration remains an explicit
deployment concern and missing adapters are recorded as durable failures.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from labelu.internal.application.service.provider_worker import run_next_provider_job
from labelu.internal.common.db import SessionLocal


def default_worker_id(prefix: str = "provider-worker") -> str:
    host = socket.gethostname() or "unknown-host"
    return f"{prefix}:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class ProviderWorkerTick:
    worker_id: str
    processed: bool
    error: str | None = None


def run_worker_once(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    runner: Callable[..., bool] = run_next_provider_job,
) -> ProviderWorkerTick:
    """Claim and process at most one queued Provider job."""

    if not str(worker_id).strip():
        raise ValueError("worker_id must not be empty")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    try:
        processed = bool(
            runner(db, worker_id=str(worker_id), lease_seconds=int(lease_seconds))
        )
        return ProviderWorkerTick(worker_id=str(worker_id), processed=processed)
    except Exception as exc:  # keep the resident loop alive after one bad tick
        return ProviderWorkerTick(
            worker_id=str(worker_id), processed=False, error=str(exc)[:1000]
        )


def run_worker_loop(
    db_factory: Callable[[], Session],
    *,
    worker_id: str | None = None,
    interval_seconds: float = 2.0,
    lease_seconds: int = 300,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    runner: Callable[..., bool] = run_next_provider_job,
) -> None:
    """Run until ``stop`` returns true, closing each short-lived Session."""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    owner = worker_id or default_worker_id()
    should_stop = stop or (lambda: False)
    while not should_stop():
        db = db_factory()
        try:
            run_worker_once(
                db,
                worker_id=owner,
                lease_seconds=lease_seconds,
                runner=runner,
            )
        finally:
            db.close()
        sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="7GAI Provider worker")
    parser.add_argument("--once", action="store_true", help="process one job and exit")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.lease_seconds < 1:
        parser.error("--lease-seconds must be positive")

    worker_id = args.worker_id or default_worker_id()
    if args.once:
        db = SessionLocal()
        try:
            result = run_worker_once(
                db,
                worker_id=worker_id,
                lease_seconds=args.lease_seconds,
            )
            print(json.dumps(result.__dict__, ensure_ascii=False), flush=True)
        finally:
            db.close()
        return 0 if result.error is None else 1

    while True:
        db = SessionLocal()
        try:
            result = run_worker_once(
                db,
                worker_id=worker_id,
                lease_seconds=args.lease_seconds,
            )
            print(json.dumps(result.__dict__, ensure_ascii=False), flush=True)
        finally:
            db.close()
        time.sleep(args.interval)


__all__ = [
    "ProviderWorkerTick",
    "default_worker_id",
    "run_worker_once",
    "run_worker_loop",
    "main",
]
