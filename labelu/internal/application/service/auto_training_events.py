from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.common.event_catalog import validate_event_name
from labelu.internal.domain.models.auto_training import AutoTrainingOutbox


def publish_event(
    db: Session,
    *,
    event_name: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    available_at: datetime | None = None,
) -> AutoTrainingOutbox:
    # Validate before touching the outbox.  Known historical names remain
    # accepted during the migration window; malformed ``7gai.*`` names and
    # unknown legacy names fail before any database mutation.
    event_name = validate_event_name(event_name, require_registered=True)
    existing = (
        db.query(AutoTrainingOutbox)
        .filter(AutoTrainingOutbox.idempotency_key == idempotency_key)
        .first()
    )
    if existing is not None:
        return existing
    row = AutoTrainingOutbox(
        event_name=event_name,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload_json=json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        idempotency_key=idempotency_key,
        status="PENDING",
        available_at=available_at or datetime.now(),
    )
    try:
        # Keep a concurrent duplicate isolated to a savepoint. Rolling back the
        # caller transaction here would also discard the business mutation that
        # produced the event.
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(AutoTrainingOutbox)
            .filter(AutoTrainingOutbox.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return existing
        raise
    return row


def enqueue_auto_training_event(
    db: Session,
    *,
    event_name: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    available_at: datetime | None = None,
) -> AutoTrainingOutbox:
    """Backward-compatible name for :func:`publish_event`."""

    return publish_event(
        db,
        event_name=event_name,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        idempotency_key=idempotency_key,
        available_at=available_at,
    )
