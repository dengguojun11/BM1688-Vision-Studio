from __future__ import annotations

from datetime import datetime, timedelta
import warnings

import pytest

from labelu.internal.application.service.auto_training_outbox import (
    OutboxDeferredError,
    claim_pending_records,
    mark_record_failed,
    mark_record_processed,
    process_outbox_batch,
)
from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_plan import (
    find_current_human_review,
    reserve_active_round,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import Base, begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingRoundSample,
    SampleHumanReview,
)


def _plan(name: str = "persistence-plan") -> AutoTrainingPlan:
    return AutoTrainingPlan(
        owner_id=1,
        name=name,
        series_type="yolo_detection",
        dataset_category_id=1,
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        status="ACTIVE",
        version=1,
        created_by=1,
        updated_by=1,
    )


def test_auto_training_lineage_does_not_create_metadata_cycles():
    plain_reference_columns = {
        "auto_training_plan": {
            "current_version_id",
            "active_round_id",
            "champion_job_id",
            "champion_export_id",
        },
        "auto_training_round": {
            "baseline_job_id",
            "training_task_id",
            "yolo_train_job_id",
            "evaluation_run_id",
            "bm1688_export_id",
            "edge_validation_job_id",
        },
    }

    for table_name, column_names in plain_reference_columns.items():
        table = Base.metadata.tables[table_name]
        for column_name in column_names:
            assert not table.c[column_name].foreign_keys

    # Accessing sorted_tables must not emit the unresolved-cycle warning used by
    # pytest cleanup and future migration tooling.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        list(Base.metadata.sorted_tables)
    assert not [warning for warning in caught if "unresolvable cycles" in str(warning.message)]


def test_round_samples_are_immutable_after_insert(db):
    with pytest.raises(ValueError, match="immutable"):
        with begin_transaction(db):
            row = AutoTrainingRoundSample(
                round_id=1,
                task_id=1,
                sample_id=1,
                split="train",
                sample_signature="a" * 64,
                annotation_sha256="b" * 64,
                image_sha256="c" * 64,
                source_sample_manifest_json="{}",
            )
            db.add(row)
            db.flush()
            row.split = "val"
            db.flush()

    db.rollback()
    with pytest.raises(ValueError, match="immutable"):
        with begin_transaction(db):
            row = AutoTrainingRoundSample(
                round_id=2,
                task_id=1,
                sample_id=2,
                split="train",
                sample_signature="d" * 64,
                annotation_sha256="e" * 64,
                image_sha256="f" * 64,
                source_sample_manifest_json="{}",
            )
            db.add(row)
            db.flush()
            db.delete(row)
            db.flush()


def test_outbox_claim_process_retry_and_dead_letter_are_persistent(db):
    now = datetime.now()
    with begin_transaction(db):
        first = AutoTrainingOutbox(
            event_name="sample.annotation.changed.v1",
            aggregate_type="task_sample",
            aggregate_id="1",
            payload_json="{}",
            idempotency_key="persistence-outbox-1",
            status="PENDING",
            available_at=now,
            max_attempts=2,
        )
        second = AutoTrainingOutbox(
            event_name="sample.annotation.changed.v1",
            aggregate_type="task_sample",
            aggregate_id="2",
            payload_json="{}",
            idempotency_key="persistence-outbox-2",
            status="PENDING",
            available_at=now,
            max_attempts=2,
        )
        db.add_all([first, second])

    with begin_transaction(db):
        claimed = claim_pending_records(
            db, owner="worker-a", now=now, lease_seconds=30, limit=1
        )
        assert len(claimed) == 1
        assert claimed[0].status == "PROCESSING"
        claimed_id = claimed[0].id

    with begin_transaction(db):
        assert claim_pending_records(
            db, owner="worker-b", now=now, lease_seconds=30, limit=10
        )[0].id != claimed_id

    with begin_transaction(db):
        row = db.query(AutoTrainingOutbox).filter_by(id=claimed_id).one()
        mark_record_failed(db, row=row, now=now, error="temporary", base_seconds=2)
        assert row.status == "RETRY"
        assert row.available_at == now + timedelta(seconds=2)

    with begin_transaction(db):
        claimed = claim_pending_records(
            db,
            owner="worker-c",
            now=now + timedelta(seconds=2),
            lease_seconds=30,
            limit=10,
        )
        row = next(item for item in claimed if item.id == claimed_id)
        mark_record_failed(
            db,
            row=row,
            now=now + timedelta(seconds=2),
            error="permanent",
            base_seconds=2,
        )
        assert row.status == "DEAD_LETTER"
        assert row.attempt_count == 2

    with begin_transaction(db):
        other = (
            db.query(AutoTrainingOutbox)
            .filter(AutoTrainingOutbox.id != claimed_id)
            .one()
        )
        mark_record_processed(db, row=other, now=now)
        assert other.status == "PROCESSED"
        assert other.processed_at == now


def test_deferred_outbox_event_does_not_consume_retry_budget(db):
    now = datetime.now()
    with begin_transaction(db):
        row = AutoTrainingOutbox(
            event_name="auto.round.train.dispatch.v1",
            aggregate_type="auto_training_round",
            aggregate_id="1",
            payload_json="{}",
            idempotency_key="deferred-outbox-event",
            status="PENDING",
            available_at=now,
            max_attempts=2,
        )
        db.add(row)
        db.flush()
        row_id = row.id

    result = process_outbox_batch(
        db,
        owner="defer-worker",
        now=now,
        handler=lambda _db, _row: (_ for _ in ()).throw(
            OutboxDeferredError("feature disabled", delay_seconds=120)
        ),
    )

    persisted = db.query(AutoTrainingOutbox).filter_by(id=row_id).one()
    assert result.deferred == 1
    assert persisted.status == "RETRY"
    assert persisted.attempt_count == 0
    assert persisted.available_at >= now + timedelta(seconds=120)


def test_current_review_must_match_current_annotation_hash(db):
    with begin_transaction(db):
        stale = SampleHumanReview(
            task_id=1,
            sample_id=99,
            annotation_sha256="a" * 64,
            review_status="APPROVED",
            reviewed_by=1,
        )
        current = SampleHumanReview(
            task_id=1,
            sample_id=99,
            annotation_sha256="b" * 64,
            review_status="APPROVED",
            reviewed_by=1,
        )
        db.add_all([stale, current])

    assert find_current_human_review(db, sample_id=99, annotation_sha256="b" * 64).id == current.id
    assert find_current_human_review(db, sample_id=99, annotation_sha256="c" * 64) is None


def test_active_round_reservation_is_closed_in_phase_one(db, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    with pytest.raises(LabelUException) as exc_info:
        reserve_active_round(
            db,
            plan_id=1,
            round_id=101,
            expected_plan_version=1,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == 57122


def test_active_round_reservation_uses_compare_and_swap_when_explicitly_enabled(
    db, monkeypatch
):
    # This is a phase-two state-machine contract, isolated from the phase-one
    # production default by an explicit test-only switch.
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    with begin_transaction(db):
        plan = _plan()
        db.add(plan)
        db.flush()
        plan_id = plan.id

    with begin_transaction(db):
        assert reserve_active_round(
            db,
            plan_id=plan_id,
            round_id=101,
            expected_plan_version=1,
        )

    with begin_transaction(db):
        assert not reserve_active_round(
            db,
            plan_id=plan_id,
            round_id=102,
            expected_plan_version=1,
        )
        persisted = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
        assert persisted.active_round_id == 101
        assert persisted.version == 2


def test_outbox_duplicate_enqueue_reuses_existing_row(db):
    with begin_transaction(db):
        first = enqueue_auto_training_event(
            db,
            event_name="sample.annotation.changed.v1",
            aggregate_type="task_sample",
            aggregate_id="77",
            payload={"task_id": 7, "sample_id": 77},
            idempotency_key="sample:77:annotation:sha",
        )
        second = enqueue_auto_training_event(
            db,
            event_name="sample.annotation.changed.v1",
            aggregate_type="task_sample",
            aggregate_id="77",
            payload={"task_id": 7, "sample_id": 77},
            idempotency_key="sample:77:annotation:sha",
        )

    assert first.id == second.id
    assert db.query(AutoTrainingOutbox).filter_by(
        idempotency_key="sample:77:annotation:sha"
    ).count() == 1
