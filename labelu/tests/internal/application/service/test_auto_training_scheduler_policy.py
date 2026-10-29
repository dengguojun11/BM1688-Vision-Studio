from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from labelu.internal.application.service.auto_training_outbox import (
    process_outbox_batch,
)
from labelu.internal.application.service.auto_training_reconciliation import (
    handle_outbox_event,
    reconcile_task_for_plan,
)
from labelu.internal.application.service.auto_training_scheduler import (
    evaluate_plan_trigger,
    evaluate_trigger_policy,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingRound,
)
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


@pytest.fixture(autouse=True)
def _enable_scheduler_switches(monkeypatch):
    monkeypatch.setenv("LABELU_AUTO_TRAINING_POOL_ENABLED", "true")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


from labelu.tests.internal.application.service.test_auto_training_reconciliation import (
    _seed_plan,
    _seed_task,
)


@pytest.mark.parametrize(
    ("new_count", "previous_count", "next_evaluation_at", "expected_reason"),
    [
        (10, 100, None, "MIN_NEW_SAMPLES"),
        (5, 40, None, "MIN_GROWTH_RATIO"),
        (1, 100, datetime(2026, 8, 9, 11, 59), "MAX_WAIT"),
    ],
)
def test_trigger_policy_accepts_any_configured_condition(
    new_count, previous_count, next_evaluation_at, expected_reason
):
    now = datetime(2026, 8, 9, 12, 0)
    decision = evaluate_trigger_policy(
        first_round=False,
        current_count=previous_count + new_count,
        previous_count=previous_count,
        new_or_changed_count=new_count,
        min_new_samples=10,
        min_growth_ratio=0.1,
        next_evaluation_at=next_evaluation_at,
        now=now,
    )

    assert decision.should_create is True
    assert expected_reason in decision.reasons


def test_trigger_policy_requires_new_data_after_first_round():
    now = datetime(2026, 8, 9, 12, 0)
    unchanged = evaluate_trigger_policy(
        first_round=False,
        current_count=50,
        previous_count=50,
        new_or_changed_count=0,
        min_new_samples=1,
        min_growth_ratio=0.0,
        next_evaluation_at=now - timedelta(days=1),
        now=now,
    )
    first = evaluate_trigger_policy(
        first_round=True,
        current_count=50,
        previous_count=0,
        new_or_changed_count=50,
        min_new_samples=1000,
        min_growth_ratio=10.0,
        next_evaluation_at=None,
        now=now,
    )

    assert unchanged.should_create is False
    assert unchanged.reasons == ("NO_NEW_DATA",)
    assert first.should_create is True
    assert first.reasons == ("FIRST_QUALIFIED_ROUND",)


def test_round_evaluation_event_creates_one_frozen_job_without_gpu_start(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="outbox-trigger-plan")
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        plan_id = plan.id

    first = process_outbox_batch(
        db, owner="scheduler-test", handler=handle_outbox_event, limit=20
    )
    assert first.processed == 1
    assert db.query(AutoTrainingRound).filter_by(plan_id=plan_id).count() == 1
    assert db.query(TrainingTask).filter_by(auto_training_plan_id=plan_id).count() == 1
    job = db.query(YoloTrainJob).filter_by(auto_training_plan_id=plan_id).one()
    assert job.status == "PENDING"

    with begin_transaction(db):
        plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
        plan.active_round_id = None
        plan.status = "ACTIVE"
        plan.next_evaluation_at = datetime.now() - timedelta(hours=1)
        db.add(
            AutoTrainingOutbox(
                event_name="auto.plan.round.evaluate.v1",
                aggregate_type="auto_training_plan",
                aggregate_id=str(plan_id),
                payload_json=f'{{"plan_id": {plan_id}}}',
                idempotency_key=f"same-data-evaluate-{plan_id}",
                status="PENDING",
            )
        )

    second = process_outbox_batch(
        db, owner="scheduler-test", handler=handle_outbox_event, limit=20
    )
    assert second.processed >= 1
    assert db.query(AutoTrainingRound).filter_by(plan_id=plan_id).count() == 1


def test_quality_reached_and_active_round_never_auto_create(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="trigger-state-gates")
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        plan.status = "AUTO_QUALITY_REACHED"
        plan_id = plan.id

    quality = evaluate_plan_trigger(db, plan_id=plan_id)
    assert quality.created is False
    assert quality.reasons == ("AUTO_QUALITY_REACHED",)

    with begin_transaction(db):
        plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
        plan.status = "ROUND_ACTIVE"
        plan.active_round_id = 999

    active = evaluate_plan_trigger(db, plan_id=plan_id)
    assert active.created is False
    assert active.reasons == ("ACTIVE_ROUND_EXISTS",)
