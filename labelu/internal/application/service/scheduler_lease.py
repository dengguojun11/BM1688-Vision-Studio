"""Database lease and fencing primitives for singleton schedulers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.scheduler_lease import SchedulerLease


AUTO_TRAINING_SCHEDULER_LEASE = "auto_training_watchdog"


@dataclass(frozen=True)
class SchedulerLeaseToken:
    lease_name: str
    owner: str
    fencing_token: int
    lease_expires_at: datetime


def _ensure_row(db: Session, lease_name: str) -> SchedulerLease:
    row = db.query(SchedulerLease).filter_by(lease_name=lease_name).one_or_none()
    if row is not None:
        return row
    row = SchedulerLease(lease_name=lease_name, fencing_token=0)
    try:
        # Use a savepoint so a concurrent insert does not abort the caller's
        # transaction; this is important when the lease is acquired before
        # the watchdog performs its normal unit of work.
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        # Another scheduler inserted the singleton concurrently; read the
        # winner after the savepoint rolls back.
        row = db.query(SchedulerLease).filter_by(lease_name=lease_name).one()
    return row


def acquire_scheduler_lease(
    db: Session,
    *,
    owner: str,
    lease_name: str = AUTO_TRAINING_SCHEDULER_LEASE,
    lease_seconds: int = 30,
    now: datetime | None = None,
) -> SchedulerLeaseToken | None:
    """Acquire or renew a scheduler lease using a database row lock.

    A new fencing token is issued whenever ownership changes (or a previous
    lease has expired).  A currently active owner may renew without changing
    its token, allowing downstream workers to reject stale owners.
    """
    if not owner.strip() or not lease_name.strip():
        raise ValueError("owner and lease_name are required")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or datetime.now()
    expires = current + timedelta(seconds=int(lease_seconds))
    with begin_transaction(db):
        row = _ensure_row(db, lease_name)
        # SQLite ignores FOR UPDATE but serializes this write transaction;
        # MySQL obtains a row lock, giving both backends the same CAS semantics.
        row = (
            db.query(SchedulerLease)
            .filter_by(lease_name=lease_name)
            .with_for_update()
            .one()
        )
        active_same_owner = (
            row.lease_owner == owner
            and row.lease_expires_at is not None
            and row.lease_expires_at > current
        )
        occupied_by_other = (
            row.lease_owner
            and row.lease_owner != owner
            and row.lease_expires_at is not None
            and row.lease_expires_at > current
        )
        if occupied_by_other:
            return None
        if not active_same_owner:
            row.fencing_token = int(row.fencing_token or 0) + 1
        row.lease_owner = owner
        row.lease_expires_at = expires
        row.heartbeat_at = current
        row.updated_at = current
        db.flush()
        return SchedulerLeaseToken(
            lease_name=lease_name,
            owner=owner,
            fencing_token=int(row.fencing_token),
            lease_expires_at=expires,
        )


def renew_scheduler_lease(
    db: Session,
    token: SchedulerLeaseToken,
    *,
    lease_seconds: int = 30,
    now: datetime | None = None,
) -> SchedulerLeaseToken | None:
    """Renew only when both owner and fencing token still match."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or datetime.now()
    expires = current + timedelta(seconds=int(lease_seconds))
    with begin_transaction(db):
        row = (
            db.query(SchedulerLease)
            .filter(
                SchedulerLease.lease_name == token.lease_name,
                SchedulerLease.lease_owner == token.owner,
                SchedulerLease.fencing_token == token.fencing_token,
                SchedulerLease.lease_expires_at.isnot(None),
                SchedulerLease.lease_expires_at > current,
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return None
        row.lease_expires_at = expires
        row.heartbeat_at = current
        row.updated_at = current
        db.flush()
        return SchedulerLeaseToken(
            lease_name=token.lease_name,
            owner=token.owner,
            fencing_token=token.fencing_token,
            lease_expires_at=expires,
        )


def scheduler_lease_is_current(
    db: Session, token: SchedulerLeaseToken, *, now: datetime | None = None
) -> bool:
    current = now or datetime.now()
    row = (
        db.query(SchedulerLease)
        .filter_by(lease_name=token.lease_name)
        .one_or_none()
    )
    return bool(
        row
        and row.lease_owner == token.owner
        and int(row.fencing_token or 0) == int(token.fencing_token)
        and row.lease_expires_at is not None
        and row.lease_expires_at > current
    )


def release_scheduler_lease(
    db: Session, token: SchedulerLeaseToken, *, now: datetime | None = None
) -> bool:
    """Release a lease only when the caller still owns its fencing token."""
    current = now or datetime.now()
    with begin_transaction(db):
        updated = (
            db.query(SchedulerLease)
            .filter(
                SchedulerLease.lease_name == token.lease_name,
                SchedulerLease.lease_owner == token.owner,
                SchedulerLease.fencing_token == token.fencing_token,
            )
            .update(
                {
                    SchedulerLease.lease_owner: None,
                    SchedulerLease.lease_expires_at: current,
                    SchedulerLease.heartbeat_at: current,
                    SchedulerLease.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        return updated == 1
