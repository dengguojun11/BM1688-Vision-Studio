"""Standalone scheduler loop for training/outbox work.

The API still starts its legacy watchdogs for compatibility.  New deployments
can run this module from a dedicated process, where the database lease ensures
only one scheduler performs a tick across multiple replicas.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
import argparse
import json
from dataclasses import asdict
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from labelu.internal.application.service.scheduler_lease import (
    AUTO_TRAINING_SCHEDULER_LEASE,
    SchedulerLeaseToken,
    acquire_scheduler_lease,
    release_scheduler_lease,
    renew_scheduler_lease,
    scheduler_lease_is_current,
)

LOG = logging.getLogger(__name__)


def default_owner(prefix: str = "training-scheduler") -> str:
    host = socket.gethostname() or "unknown-host"
    return f"{prefix}:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class SchedulerTickResult:
    acquired: bool
    fencing_token: int | None = None
    ticked: bool = False
    released: bool = False
    error: str | None = None


def run_scheduler_once(
    db: Session,
    *,
    tick: Callable[[Session, SchedulerLeaseToken], None],
    owner: str,
    lease_name: str = AUTO_TRAINING_SCHEDULER_LEASE,
    lease_seconds: int = 30,
) -> SchedulerTickResult:
    """Run one fenced scheduler tick, releasing the lease on exit."""

    token = acquire_scheduler_lease(
        db,
        owner=owner,
        lease_name=lease_name,
        lease_seconds=lease_seconds,
    )
    if token is None:
        return SchedulerTickResult(acquired=False)
    ticked = False
    error: str | None = None
    try:
        if scheduler_lease_is_current(db, token):
            tick(db, token)
            ticked = True
        else:
            error = "scheduler lease is no longer current"
    except Exception as exc:
        LOG.exception("training scheduler tick failed owner=%s", owner)
        error = str(exc)[:1000]
    finally:
        try:
            released = release_scheduler_lease(db, token)
        except Exception:
            LOG.exception("training scheduler lease release failed owner=%s", owner)
            released = False
    return SchedulerTickResult(
        acquired=True,
        fencing_token=token.fencing_token,
        ticked=ticked,
        released=released,
        error=error,
    )


def run_scheduler_loop(
    db_factory: Callable[[], Session],
    *,
    tick: Callable[[Session, SchedulerLeaseToken], None],
    owner: str | None = None,
    interval_seconds: float = 5.0,
    lease_name: str = AUTO_TRAINING_SCHEDULER_LEASE,
    lease_seconds: int = 30,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run a bounded-interval leader loop until ``stop`` returns true."""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    scheduler_owner = owner or default_owner()
    should_stop = stop or (lambda: False)
    while not should_stop():
        db = db_factory()
        try:
            run_scheduler_once(
                db,
                tick=tick,
                owner=scheduler_owner,
                lease_name=lease_name,
                lease_seconds=lease_seconds,
            )
        finally:
            db.close()
        sleep(interval_seconds)


__all__ = [
    "SchedulerTickResult",
    "default_owner",
    "run_scheduler_once",
    "run_scheduler_loop",
    "renew_scheduler_lease",
]


def main(argv: list[str] | None = None) -> int:
    """Run the existing auto-training cycle from a dedicated process.

    The cycle retains its established switches and outbox handler while this
    entrypoint keeps it out of the API process when operators deploy a
    dedicated scheduler.  ``--once`` is useful for health checks and tests.
    """

    parser = argparse.ArgumentParser(description="7GAI training scheduler")
    parser.add_argument(
        "--once", action="store_true", help="run one scheduler cycle and exit"
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, help="seconds between cycles"
    )
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    from labelu.internal.application.service.auto_training_watchdog import (
        run_auto_training_watchdog_cycle,
    )

    scheduler_owner = default_owner()
    while True:
        result = run_auto_training_watchdog_cycle(owner=scheduler_owner)
        print(json.dumps(asdict(result), ensure_ascii=False, default=str), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover - exercised by deployment smoke tests
    raise SystemExit(main())
