from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

import pytest
from sqlalchemy.orm import sessionmaker

from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import AutoTrainingOutbox
from labelu.internal.application.service.auto_training_outbox import (
    OutboxRecord,
    OutboxStatus,
    claim_record,
    claim_pending_records,
    complete_record,
    fail_record,
    mark_record_failed,
    mark_record_processed,
    OutboxLeaseLost,
    process_outbox_batch,
    run_outbox_loop,
)

NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def _record(**changes) -> OutboxRecord:
    values = {
        "id": 1,
        "idempotency_key": "sample:1:annotation:v1",
        "status": OutboxStatus.PENDING,
        "attempt_count": 0,
        "available_at": NOW,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    values.update(changes)
    return OutboxRecord(**values)


def test_claim_is_conditional_and_idempotent():
    claimed = claim_record(_record(), owner="worker-a", now=NOW, lease_seconds=60)

    assert claimed.status == OutboxStatus.PROCESSING
    assert claimed.lease_owner == "worker-a"
    assert claim_record(claimed, owner="worker-b", now=NOW, lease_seconds=60) is None


def test_expired_lease_can_be_recovered():
    stale = _record(
        status=OutboxStatus.PROCESSING,
        lease_owner="dead-worker",
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    claimed = claim_record(stale, owner="worker-b", now=NOW, lease_seconds=30)

    assert claimed.lease_owner == "worker-b"
    assert claimed.lease_expires_at == NOW + timedelta(seconds=30)


def test_retry_uses_bounded_exponential_backoff_then_dead_letters():
    first = fail_record(
        _record(status=OutboxStatus.PROCESSING),
        now=NOW,
        max_attempts=3,
        base_seconds=10,
        max_backoff_seconds=60,
        error="temporary",
    )
    second = fail_record(
        first,
        now=NOW,
        max_attempts=3,
        base_seconds=10,
        max_backoff_seconds=60,
        error="temporary",
    )
    final = fail_record(
        second,
        now=NOW,
        max_attempts=3,
        base_seconds=10,
        max_backoff_seconds=60,
        error="permanent",
    )

    assert first.status == OutboxStatus.RETRY
    assert first.available_at == NOW + timedelta(seconds=10)
    assert second.available_at == NOW + timedelta(seconds=20)
    assert final.status == OutboxStatus.DEAD_LETTER


def test_processed_record_cannot_be_reclaimed_or_failed():
    completed = complete_record(
        _record(status=OutboxStatus.PROCESSING, lease_owner="worker-a"),
        now=NOW,
    )

    assert completed.status == OutboxStatus.PROCESSED
    assert claim_record(completed, owner="worker-b", now=NOW, lease_seconds=60) is None
    assert (
        fail_record(
            completed,
            now=NOW,
            max_attempts=3,
            base_seconds=10,
            max_backoff_seconds=60,
            error="late failure",
        )
        == completed
    )


def test_expired_processing_lease_recovers_after_session_restart(db):
    expired_at = datetime.now() - timedelta(minutes=1)
    with begin_transaction(db):
        row = AutoTrainingOutbox(
            event_name="test.persisted.event.v1",
            aggregate_type="test",
            aggregate_id="1",
            payload_json="{}",
            idempotency_key="test:persisted:expired-lease",
            status="PROCESSING",
            available_at=expired_at,
            attempt_count=0,
            max_attempts=3,
            lease_owner="dead-worker",
            lease_expires_at=expired_at,
        )
        db.add(row)
        db.flush()
        row_id = row.id

    restarted_session_factory = sessionmaker(autoflush=False, bind=db.get_bind())
    restarted = restarted_session_factory()
    handled = []
    try:
        result = process_outbox_batch(
            restarted,
            owner="replacement-worker",
            handler=lambda _db, event: handled.append(event.id),
            lease_seconds=30,
            limit=1,
        )
    finally:
        restarted.close()

    assert result.claimed == 1
    assert result.processed == 1
    assert handled == [row_id]

    reopened = restarted_session_factory()
    try:
        persisted = reopened.query(AutoTrainingOutbox).filter_by(id=row_id).one()
        assert persisted.status == "PROCESSED"
        assert persisted.lease_owner is None
        assert persisted.lease_expires_at is None
        replay = process_outbox_batch(
            reopened,
            owner="third-worker",
            handler=lambda *_args: handled.append(-1),
            limit=1,
        )
    finally:
        reopened.close()

    assert replay.claimed == 0
    assert handled == [row_id]


def test_persisted_terminal_outbox_states_never_regress(db):
    with begin_transaction(db):
        processed = AutoTrainingOutbox(
            event_name="test.processed.event.v1",
            aggregate_type="test",
            aggregate_id="1",
            payload_json="{}",
            idempotency_key="test:persisted:processed",
            status="PROCESSED",
            available_at=datetime.now(),
            processed_at=datetime.now(),
        )
        dead_letter = AutoTrainingOutbox(
            event_name="test.dead.event.v1",
            aggregate_type="test",
            aggregate_id="2",
            payload_json="{}",
            idempotency_key="test:persisted:dead",
            status="DEAD_LETTER",
            available_at=datetime.now(),
            attempt_count=5,
            max_attempts=5,
        )
        db.add_all([processed, dead_letter])
        db.flush()

        assert mark_record_processed(db, row=processed, now=datetime.now()) is processed
        assert (
            mark_record_failed(
                db,
                row=processed,
                now=datetime.now(),
                error="late failure",
            )
            is processed
        )
        assert (
            mark_record_failed(
                db,
                row=dead_letter,
                now=datetime.now(),
                error="late retry",
            )
            is dead_letter
        )

    assert processed.status == "PROCESSED"
    assert dead_letter.status == "DEAD_LETTER"
    assert dead_letter.attempt_count == 5


def test_reclaimed_row_cannot_be_finalized_by_stale_worker(db):
    now = datetime.now()
    with begin_transaction(db):
        row = AutoTrainingOutbox(
            event_name="test.lease.race.v1",
            aggregate_type="test",
            aggregate_id="1",
            payload_json="{}",
            idempotency_key="test:lease:race",
            status="PENDING",
            available_at=now,
            max_attempts=3,
        )
        db.add(row)
        db.flush()
        row_id = row.id

    with begin_transaction(db):
        stale = claim_pending_records(
            db, owner="worker-a", now=now, lease_seconds=1, limit=1
        )[0]

    with begin_transaction(db):
        replacement = claim_pending_records(
            db,
            owner="worker-b",
            now=now + timedelta(seconds=2),
            lease_seconds=30,
            limit=1,
        )[0]
        assert replacement.id == row_id

    with begin_transaction(db):
        with pytest.raises(OutboxLeaseLost):
            mark_record_processed(
                db,
                row=stale,
                owner="worker-a",
                now=now + timedelta(seconds=2),
            )

    current = db.query(AutoTrainingOutbox).filter_by(id=row_id).one()
    assert current.status == "PROCESSING"
    assert current.lease_owner == "worker-b"


def test_resident_outbox_loop_closes_sessions_and_survives_cycle_failure(monkeypatch):
    sessions = []
    calls = []
    stop_calls = iter([False, True])

    class Session:
        def rollback(self):
            calls.append("rollback")

        def close(self):
            sessions.append("closed")

    def process(_db, **_kwargs):
        calls.append("process")
        raise RuntimeError("temporary database failure")

    monkeypatch.setattr(
        "labelu.internal.application.service.auto_training_outbox.process_outbox_batch",
        process,
    )
    run_outbox_loop(
        lambda: Session(),
        owner="resident-test",
        handler=lambda *_args: None,
        interval_seconds=0.01,
        stop=lambda: next(stop_calls),
        sleep=lambda _seconds: calls.append("sleep"),
    )
    assert calls == ["process", "rollback", "sleep"]
    assert sessions == ["closed"]


def test_resident_outbox_loop_forwards_heartbeat_configuration(monkeypatch):
    calls = []
    stop_calls = iter([False, True])
    heartbeat = lambda _record_id, _owner: True

    class Session:
        def close(self):
            calls.append("closed")

    def process(_db, **kwargs):
        calls.append((kwargs["heartbeat"], kwargs["heartbeat_interval_seconds"]))

    monkeypatch.setattr(
        "labelu.internal.application.service.auto_training_outbox.process_outbox_batch",
        process,
    )
    run_outbox_loop(
        lambda: Session(),
        owner="resident-heartbeat-test",
        handler=lambda *_args: None,
        lease_seconds=30,
        heartbeat=heartbeat,
        heartbeat_interval_seconds=3,
        interval_seconds=0.01,
        stop=lambda: next(stop_calls),
        sleep=lambda _seconds: None,
    )
    assert calls == [(heartbeat, 3), "closed"]


def test_resident_outbox_loop_rejects_invalid_heartbeat_interval():
    with pytest.raises(ValueError, match="heartbeat_interval_seconds"):
        run_outbox_loop(
            lambda: None,
            owner="resident-invalid-heartbeat-test",
            handler=lambda *_args: None,
            heartbeat_interval_seconds=0,
        )


def test_outbox_heartbeat_loss_prevents_terminal_ack(db):
    now = datetime.now()
    with begin_transaction(db):
        db.add(
            AutoTrainingOutbox(
                event_name="test.heartbeat.v1",
                aggregate_type="test",
                aggregate_id="1",
                payload_json="{}",
                idempotency_key="test:heartbeat",
                status="PENDING",
                available_at=now,
                max_attempts=3,
            )
        )

    result = process_outbox_batch(
        db,
        owner="heartbeat-worker",
        handler=lambda _db, _row: time.sleep(0.03),
        lease_seconds=60,
        heartbeat=lambda _record_id, _owner: False,
        heartbeat_interval_seconds=0.01,
    )
    assert result.claimed == 1
    assert result.processed == 0
    assert result.lease_lost == 1
    row = db.query(AutoTrainingOutbox).filter_by(idempotency_key="test:heartbeat").one()
    assert row.status == "PROCESSING"
    assert row.lease_owner == "heartbeat-worker"


def test_claim_predicates_are_rechecked_atomically_and_leave_filtered_rows_unchanged(
    db,
):
    now = datetime.now()
    with begin_transaction(db):
        db.add_all(
            [
                AutoTrainingOutbox(
                    event_name="training.worker.command.v1",
                    aggregate_type="yolo_train_job",
                    aggregate_id="manual",
                    payload_json="{}",
                    idempotency_key="claim-filter-manual",
                    status="PENDING",
                    available_at=now,
                ),
                AutoTrainingOutbox(
                    event_name="training.worker.command.v1",
                    aggregate_type="yolo_train_job",
                    aggregate_id="automatic",
                    payload_json="{}",
                    idempotency_key="claim-filter-automatic",
                    status="PENDING",
                    available_at=now,
                ),
            ]
        )

    predicate = AutoTrainingOutbox.aggregate_id == "manual"
    claimed = claim_pending_records(
        db,
        owner="claim-filter-worker-a",
        now=now,
        lease_seconds=60,
        claim_predicates=(predicate,),
    )
    competing = claim_pending_records(
        db,
        owner="claim-filter-worker-b",
        now=now,
        lease_seconds=60,
        claim_predicates=(predicate,),
    )

    assert [row.aggregate_id for row in claimed] == ["manual"]
    assert competing == []
    filtered = (
        db.query(AutoTrainingOutbox)
        .filter_by(idempotency_key="claim-filter-automatic")
        .one()
    )
    assert filtered.status == "PENDING"
    assert filtered.lease_owner is None
    assert filtered.lease_expires_at is None
