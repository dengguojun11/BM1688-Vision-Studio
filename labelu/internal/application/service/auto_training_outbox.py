from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
import logging
import os
import socket
import threading
import time
import uuid
from typing import Any, Callable, Collection

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from labelu.internal.domain.models.auto_training import AutoTrainingOutbox

LOG = logging.getLogger(__name__)


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"


class OutboxDeferredError(RuntimeError):
    def __init__(self, message: str, *, delay_seconds: int = 60):
        super().__init__(message)
        self.delay_seconds = max(1, int(delay_seconds))


class OutboxLeaseLost(RuntimeError):
    """Raised when a handler tries to update a row leased by another worker."""


@dataclass(frozen=True)
class OutboxRecord:
    id: int
    idempotency_key: str
    status: OutboxStatus
    attempt_count: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error: str | None = None
    processed_at: datetime | None = None


@dataclass(frozen=True)
class OutboxBatchResult:
    claimed: int = 0
    processed: int = 0
    retried: int = 0
    dead_lettered: int = 0
    deferred: int = 0
    lease_lost: int = 0


def default_outbox_owner(prefix: str = "training-outbox") -> str:
    host = socket.gethostname() or "unknown-host"
    return f"{prefix}:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def claim_record(
    record: OutboxRecord, *, owner: str, now: datetime, lease_seconds: int
) -> OutboxRecord | None:
    eligible_status = record.status in {OutboxStatus.PENDING, OutboxStatus.RETRY}
    expired_processing = (
        record.status == OutboxStatus.PROCESSING
        and record.lease_expires_at is not None
        and record.lease_expires_at <= now
    )
    if not (eligible_status or expired_processing) or record.available_at > now:
        return None
    return replace(
        record,
        status=OutboxStatus.PROCESSING,
        lease_owner=owner,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
    )


def complete_record(record: OutboxRecord, *, now: datetime) -> OutboxRecord:
    if record.status == OutboxStatus.PROCESSED:
        return record
    if record.status != OutboxStatus.PROCESSING:
        raise ValueError("only processing records can be completed")
    return replace(
        record,
        status=OutboxStatus.PROCESSED,
        lease_owner=None,
        lease_expires_at=None,
        processed_at=now,
    )


def fail_record(
    record: OutboxRecord,
    *,
    now: datetime,
    max_attempts: int,
    base_seconds: int,
    max_backoff_seconds: int,
    error: str,
) -> OutboxRecord:
    if record.status in {OutboxStatus.PROCESSED, OutboxStatus.DEAD_LETTER}:
        return record
    attempts = record.attempt_count + 1
    if attempts >= max_attempts:
        return replace(
            record,
            status=OutboxStatus.DEAD_LETTER,
            attempt_count=attempts,
            lease_owner=None,
            lease_expires_at=None,
            last_error=error,
        )
    delay = min(base_seconds * (2 ** (attempts - 1)), max_backoff_seconds)
    return replace(
        record,
        status=OutboxStatus.RETRY,
        attempt_count=attempts,
        available_at=now + timedelta(seconds=delay),
        lease_owner=None,
        lease_expires_at=None,
        last_error=error,
    )


def _claimable_predicate(
    now: datetime,
    event_names: Collection[str] | None = None,
    claim_predicates: Collection[Any] | None = None,
):
    predicates = [
        AutoTrainingOutbox.available_at <= now,
        or_(
            AutoTrainingOutbox.status.in_(("PENDING", "RETRY")),
            and_(
                AutoTrainingOutbox.status == "PROCESSING",
                AutoTrainingOutbox.lease_expires_at.isnot(None),
                AutoTrainingOutbox.lease_expires_at <= now,
            ),
        ),
    ]
    if event_names is not None:
        names = tuple(str(value) for value in event_names if str(value).strip())
        if not names:
            return and_(False)
        predicates.append(AutoTrainingOutbox.event_name.in_(names))
    if claim_predicates is not None:
        predicates.extend(claim_predicates)
    return and_(*predicates)


def claim_pending_records(
    db: Session,
    *,
    owner: str,
    now: datetime,
    lease_seconds: int = 60,
    limit: int = 20,
    event_names: Collection[str] | None = None,
    claim_predicates: Collection[Any] | None = None,
) -> list[AutoTrainingOutbox]:
    """Atomically lease due events so multiple workers cannot process one row."""
    if not owner.strip():
        raise ValueError("owner is required")
    if lease_seconds < 1 or limit < 1:
        raise ValueError("lease_seconds and limit must be positive")

    candidates = (
        db.query(AutoTrainingOutbox.id)
        .filter(_claimable_predicate(now, event_names, claim_predicates))
        .order_by(AutoTrainingOutbox.available_at, AutoTrainingOutbox.id)
        .limit(limit)
        .all()
    )
    claimed: list[AutoTrainingOutbox] = []
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    for (record_id,) in candidates:
        updated = (
            db.query(AutoTrainingOutbox)
            .filter(
                AutoTrainingOutbox.id == record_id,
                _claimable_predicate(now, event_names, claim_predicates),
            )
            .update(
                {
                    AutoTrainingOutbox.status: "PROCESSING",
                    AutoTrainingOutbox.lease_owner: owner,
                    AutoTrainingOutbox.lease_expires_at: lease_expires_at,
                    AutoTrainingOutbox.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if updated == 1:
            claimed.append(
                db.query(AutoTrainingOutbox)
                .filter(AutoTrainingOutbox.id == record_id)
                .one()
            )
    return claimed


def renew_record_lease(
    db: Session,
    *,
    record_id: int,
    owner: str,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> bool:
    """Extend one active lease only while its owner is still current."""
    if not owner.strip():
        raise ValueError("owner is required")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or datetime.now()
    expires = current + timedelta(seconds=lease_seconds)
    updated = (
        db.query(AutoTrainingOutbox)
        .filter(
            AutoTrainingOutbox.id == int(record_id),
            AutoTrainingOutbox.status == "PROCESSING",
            AutoTrainingOutbox.lease_owner == owner,
            AutoTrainingOutbox.lease_expires_at.isnot(None),
            AutoTrainingOutbox.lease_expires_at > current,
        )
        .update(
            {
                AutoTrainingOutbox.lease_expires_at: expires,
                AutoTrainingOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return updated == 1


def mark_record_processed(
    db: Session,
    *,
    row: AutoTrainingOutbox,
    now: datetime,
    owner: str | None = None,
) -> AutoTrainingOutbox:
    if row.status == "PROCESSED":
        return row
    if row.status != "PROCESSING":
        raise ValueError("only processing records can be completed")
    current = _owned_processing_row(db, row=row, owner=owner, now=now)
    current.status = "PROCESSED"
    current.processed_at = now
    current.lease_owner = None
    current.lease_expires_at = None
    current.last_error = None
    current.updated_at = now
    db.flush()
    return current


def mark_record_failed(
    db: Session,
    *,
    row: AutoTrainingOutbox,
    now: datetime,
    error: str,
    base_seconds: int = 5,
    max_backoff_seconds: int = 3600,
    owner: str | None = None,
) -> AutoTrainingOutbox:
    if row.status in {"PROCESSED", "DEAD_LETTER"}:
        return row
    if row.status != "PROCESSING":
        raise ValueError("only processing records can fail")
    current = _owned_processing_row(db, row=row, owner=owner, now=now)
    attempts = current.attempt_count + 1
    current.attempt_count = attempts
    current.lease_owner = None
    current.lease_expires_at = None
    current.last_error = error[:4000]
    current.updated_at = now
    if attempts >= current.max_attempts:
        current.status = "DEAD_LETTER"
    else:
        delay = min(base_seconds * (2 ** (attempts - 1)), max_backoff_seconds)
        current.status = "RETRY"
        current.available_at = now + timedelta(seconds=delay)
    db.flush()
    return current


def mark_record_deferred(
    db: Session,
    *,
    row: AutoTrainingOutbox,
    now: datetime,
    delay_seconds: int,
    reason: str,
    owner: str | None = None,
) -> AutoTrainingOutbox:
    if row.status != "PROCESSING":
        raise ValueError("only processing records can be deferred")
    current = _owned_processing_row(db, row=row, owner=owner, now=now)
    current.status = "RETRY"
    current.available_at = now + timedelta(seconds=max(1, int(delay_seconds)))
    current.lease_owner = None
    current.lease_expires_at = None
    current.last_error = reason[:4000]
    current.updated_at = now
    db.flush()
    return current


def _owned_processing_row(
    db: Session,
    *,
    row: AutoTrainingOutbox,
    owner: str | None,
    now: datetime,
) -> AutoTrainingOutbox:
    """Reload and verify ownership before a terminal state write.

    ``handler`` execution can outlive the lease.  A stale process must never
    retry, defer, or complete a row that a replacement worker has reclaimed.
    The optional owner preserves the legacy direct helper API used by tests and
    administrative tooling; production batch processing always supplies it.
    """
    query = (
        db.query(AutoTrainingOutbox)
        .filter(
            AutoTrainingOutbox.id == row.id,
            AutoTrainingOutbox.status == "PROCESSING",
        )
        .populate_existing()
    )
    if owner is not None:
        query = query.filter(AutoTrainingOutbox.lease_owner == owner)
    current = query.one_or_none()
    if current is None:
        raise OutboxLeaseLost(f"outbox lease lost for row {row.id}")
    if owner is not None and (
        current.lease_expires_at is None or current.lease_expires_at <= now
    ):
        raise OutboxLeaseLost(f"outbox lease expired for row {row.id}")
    return current


def process_outbox_batch(
    db: Session,
    *,
    owner: str,
    handler: Callable[[Session, AutoTrainingOutbox], None],
    now: datetime | None = None,
    lease_seconds: int = 60,
    limit: int = 20,
    event_names: Collection[str] | None = None,
    claim_predicates: Collection[Any] | None = None,
    heartbeat: Callable[[int, str], bool] | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> OutboxBatchResult:
    """Claim and process a bounded batch using short, recoverable transactions."""
    from labelu.internal.common.db import begin_transaction

    current = now or datetime.now()
    with begin_transaction(db):
        claimed_rows = claim_pending_records(
            db,
            owner=owner,
            now=current,
            lease_seconds=lease_seconds,
            limit=limit,
            event_names=event_names,
            claim_predicates=claim_predicates,
        )
        claimed_ids = [row.id for row in claimed_rows]

    processed = retried = dead_lettered = deferred = lease_lost = 0
    for record_id in claimed_ids:
        heartbeat_stop = threading.Event()
        heartbeat_lost = threading.Event()
        heartbeat_thread = None
        if heartbeat is not None:
            interval = heartbeat_interval_seconds
            if interval is None:
                interval = max(1.0, min(float(lease_seconds) / 3.0, 30.0))
            if interval <= 0:
                raise ValueError("heartbeat_interval_seconds must be positive")

            def _heartbeat_loop(
                current_record_id: int = record_id,
                current_owner: str = owner,
                current_interval: float = float(interval),
            ) -> None:
                while not heartbeat_stop.wait(current_interval):
                    try:
                        if not heartbeat(current_record_id, current_owner):
                            heartbeat_lost.set()
                            return
                    except Exception:
                        heartbeat_lost.set()
                        LOG.exception(
                            "outbox heartbeat failed row=%s owner=%s",
                            current_record_id,
                            current_owner,
                        )
                        return

            heartbeat_thread = threading.Thread(
                target=_heartbeat_loop,
                name=f"outbox-heartbeat-{record_id}",
                daemon=True,
            )
            heartbeat_thread.start()
        try:
            with begin_transaction(db):
                row = (
                    db.query(AutoTrainingOutbox)
                    .filter(
                        AutoTrainingOutbox.id == record_id,
                        AutoTrainingOutbox.status == "PROCESSING",
                        AutoTrainingOutbox.lease_owner == owner,
                    )
                    .one_or_none()
                )
                if row is None:
                    continue
                if heartbeat_lost.is_set():
                    raise OutboxLeaseLost(f"outbox heartbeat lost for row {record_id}")
                handler(db, row)
                if heartbeat_lost.is_set():
                    raise OutboxLeaseLost(f"outbox heartbeat lost for row {record_id}")
                mark_record_processed(db, row=row, now=datetime.now(), owner=owner)
                processed += 1
        except OutboxDeferredError as exc:
            db.rollback()
            try:
                with begin_transaction(db):
                    row = db.query(AutoTrainingOutbox).filter_by(id=record_id).one()
                    mark_record_deferred(
                        db,
                        row=row,
                        now=datetime.now(),
                        delay_seconds=exc.delay_seconds,
                        reason=str(exc),
                        owner=owner,
                    )
                    deferred += 1
            except OutboxLeaseLost:
                lease_lost += 1
                LOG.warning("outbox lease lost while deferring row %s", record_id)
        except OutboxLeaseLost:
            db.rollback()
            lease_lost += 1
            LOG.warning("outbox lease lost while completing row %s", record_id)
        except Exception as exc:
            db.rollback()
            try:
                with begin_transaction(db):
                    row = db.query(AutoTrainingOutbox).filter_by(id=record_id).one()
                    marked = mark_record_failed(
                        db, row=row, now=datetime.now(), error=str(exc), owner=owner
                    )
                    if marked.status == "DEAD_LETTER":
                        dead_lettered += 1
                    else:
                        retried += 1
            except OutboxLeaseLost:
                lease_lost += 1
                LOG.warning("outbox lease lost while retrying row %s", record_id)
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(
                    timeout=max(1.0, float(heartbeat_interval_seconds or 1.0))
                )
    return OutboxBatchResult(
        claimed=len(claimed_ids),
        processed=processed,
        retried=retried,
        dead_lettered=dead_lettered,
        deferred=deferred,
        lease_lost=lease_lost,
    )


def run_outbox_loop(
    db_factory: Callable[[], Session],
    *,
    handler: Callable[[Session, AutoTrainingOutbox], None],
    owner: str | None = None,
    interval_seconds: float = 5.0,
    lease_seconds: int = 60,
    limit: int = 20,
    event_names: Collection[str] | None = None,
    claim_predicates: Collection[Any] | None = None,
    heartbeat: Callable[[int, str], bool] | None = None,
    heartbeat_interval_seconds: float | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run a recoverable Outbox consumer until ``stop`` requests shutdown."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if lease_seconds < 1 or limit < 1:
        raise ValueError("lease_seconds and limit must be positive")
    if heartbeat_interval_seconds is not None and heartbeat_interval_seconds <= 0:
        raise ValueError("heartbeat_interval_seconds must be positive")
    consumer_owner = owner or default_outbox_owner()
    should_stop = stop or (lambda: False)
    while not should_stop():
        db = db_factory()
        try:
            process_outbox_batch(
                db,
                owner=consumer_owner,
                handler=handler,
                lease_seconds=lease_seconds,
                limit=limit,
                event_names=event_names,
                claim_predicates=claim_predicates,
                heartbeat=heartbeat,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
        except Exception:
            # A database outage or malformed row must not terminate the
            # resident consumer; the next cycle can recover after rollback.
            LOG.exception("outbox consumer cycle failed owner=%s", consumer_owner)
            try:
                db.rollback()
            except Exception:
                LOG.exception(
                    "outbox consumer rollback failed owner=%s", consumer_owner
                )
        finally:
            db.close()
        sleep(interval_seconds)


__all__ = [
    "OutboxStatus",
    "OutboxDeferredError",
    "OutboxLeaseLost",
    "OutboxRecord",
    "OutboxBatchResult",
    "default_outbox_owner",
    "claim_record",
    "complete_record",
    "fail_record",
    "claim_pending_records",
    "renew_record_lease",
    "mark_record_processed",
    "mark_record_failed",
    "mark_record_deferred",
    "process_outbox_batch",
    "run_outbox_loop",
]
