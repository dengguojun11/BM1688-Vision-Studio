from __future__ import annotations

import json

from labelu.internal.application.service.auto_training_lifecycle import (
    record_quality_evaluation,
    record_quality_evaluation_from_job,
    synchronize_training_job,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _seed(db, *, max_failures: int = 2):
    config = {
        "training_policy": {"max_technical_failures": max_failures},
        "quality_policy_binding": {"signature": "q" * 64},
        "evaluation_binding": {"signature": "e" * 64},
        "auto_stop": {"overall_recall_threshold": 0.95},
    }
    with begin_transaction(db):
        plan = AutoTrainingPlan(
            owner_id=1,
            name="lifecycle-plan",
            series_type="yolo_detection",
            dataset_category_id=1,
            label_set_signature="a" * 64,
            class_order_signature="b" * 64,
            status="ROUND_ACTIVE",
            version=1,
            created_by=1,
            updated_by=1,
        )
        db.add(plan)
        db.flush()
        version = AutoTrainingPlanVersion(
            plan_id=plan.id,
            version_no=1,
            status="APPROVED",
            config_json=json.dumps(config),
            config_sha256="c" * 64,
            quality_policy_signature="q" * 64,
            evaluation_binding_json=json.dumps(config["evaluation_binding"]),
            missing_fields_json="[]",
            validation_errors_json="[]",
            created_by=1,
        )
        db.add(version)
        db.flush()
        plan.current_version_id = version.id
        round_row = AutoTrainingRound(
            plan_id=plan.id,
            plan_version_id=version.id,
            round_no=1,
            state="QUEUED",
            trigger_type="AUTO",
            trigger_snapshot_json="{}",
            dataset_signature="d" * 64,
            execution_signature="x" * 64,
            stage_status_json=json.dumps({"SNAPSHOT": "COMPLETED", "TRAIN": "QUEUED"}),
            stage_errors_json="{}",
            retry_count_json="{}",
        )
        db.add(round_row)
        db.flush()
        plan.active_round_id = round_row.id
        job = YoloTrainJob(
            task_id=1,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            auto_training_plan_id=plan.id,
            auto_training_round_id=round_row.id,
            execution_signature="t" * 64,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        round_row.yolo_train_job_id = job.id
        ids = plan.id, round_row.id, job.id
    return ids


def test_completed_training_moves_to_evaluation_and_enqueues_once(db):
    _plan_id, round_id, job_id = _seed(db)
    with begin_transaction(db):
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"

    with begin_transaction(db):
        result = synchronize_training_job(db, job_id=job_id)
    with begin_transaction(db):
        duplicate = synchronize_training_job(db, job_id=job_id)

    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    events = db.query(AutoTrainingOutbox).filter_by(
        event_name="auto.round.quality.evaluate.v1"
    ).all()
    assert result.changed is True
    assert duplicate.changed is False
    assert round_row.state == "PT_EVALUATING"
    assert len(events) == 1


def test_technical_failures_retry_then_pause_safely(db):
    plan_id, round_id, job_id = _seed(db, max_failures=2)
    with begin_transaction(db):
        db.query(YoloTrainJob).filter_by(id=job_id).one().status = "FAILED"

    with begin_transaction(db):
        first = synchronize_training_job(db, job_id=job_id)
    job = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert first.action == "RETRY_QUEUED"
    assert job.status == "PENDING"

    with begin_transaction(db):
        job.status = "FAILED"
        job.error_message = "failed again"
    with begin_transaction(db):
        second = synchronize_training_job(db, job_id=job_id)

    plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert second.action == "SAFETY_PAUSED"
    assert round_row.state == "TRAIN_FAILED"
    assert plan.status == "PAUSED_SAFETY"
    assert plan.active_round_id is None


def test_quality_boundary_and_low_label_warning(db):
    plan_id, round_id, job_id = _seed(db)
    with begin_transaction(db):
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        synchronize_training_job(db, job_id=job_id)

    with begin_transaction(db):
        failed = record_quality_evaluation(
            db,
            round_id=round_id,
            overall_metrics={"overall_recall": 0.9499},
            per_label_metrics={"helmet": {"recall": 0.99}},
            evidence_manifest_rel_path="evidence/failed.json",
            evidence_manifest_sha256="f" * 64,
        )
    assert failed.decision_code == "QUALITY_FAILED"
    assert not db.query(AutoTrainingOutbox).filter_by(
        event_name="auto.round.bm1688.requested.v1"
    ).count()

    # A new round is used to verify the inclusive 0.9500 threshold.
    with begin_transaction(db):
        plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
        round_row = AutoTrainingRound(
            plan_id=plan.id,
            plan_version_id=plan.current_version_id,
            round_no=2,
            state="PT_EVALUATING",
            trigger_type="AUTO",
            trigger_snapshot_json="{}",
            dataset_signature="g" * 64,
            execution_signature="y" * 64,
            stage_status_json="{}",
            stage_errors_json="{}",
            retry_count_json="{}",
        )
        db.add(round_row)
        db.flush()
        plan.active_round_id = round_row.id
        reached_round_id = round_row.id
    with begin_transaction(db):
        reached = record_quality_evaluation(
            db,
            round_id=reached_round_id,
            overall_metrics={"overall_recall": 0.9500},
            per_label_metrics={"plate": {"recall": 0.72}},
            evidence_manifest_rel_path="evidence/reached.json",
            evidence_manifest_sha256="z" * 64,
        )

    plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
    evaluation = db.query(AutoTrainingEvaluationRun).filter_by(
        round_id=reached_round_id
    ).one()
    events = db.query(AutoTrainingOutbox).filter_by(
        event_name="auto.round.bm1688.requested.v1",
        aggregate_id=str(reached_round_id),
    ).all()
    assert reached.decision_code == "AUTO_QUALITY_REACHED"
    assert plan.status == "AUTO_QUALITY_REACHED"
    assert "LABEL_RECALL_BELOW_AUTO_STOP_THRESHOLD:plate" in evaluation.decision_json
    assert len(events) == 1


def test_completed_external_evidence_makes_stale_outbox_delivery_idempotent(db):
    _plan_id, round_id, job_id = _seed(db)
    with begin_transaction(db):
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        synchronize_training_job(db, job_id=job_id)
        record_quality_evaluation(
            db,
            round_id=round_id,
            overall_metrics={"overall_recall": 0.95},
            per_label_metrics={},
            evidence_manifest_rel_path="evidence/external.json",
            evidence_manifest_sha256="e" * 64,
        )

    with begin_transaction(db):
        result = record_quality_evaluation_from_job(
            db, round_id=round_id, job_id=job_id
        )

    assert result.changed is False
    assert result.decision_code == "AUTO_QUALITY_REACHED"
