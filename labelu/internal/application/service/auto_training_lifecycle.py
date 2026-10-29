from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_outbox import OutboxDeferredError
from labelu.internal.application.service.auto_training_state_machine import (
    RoundStatus,
    apply_quality_result,
)
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


@dataclass(frozen=True)
class LifecycleResult:
    changed: bool
    action: str
    round_id: int
    decision_code: str | None = None


def _loads(raw: str | None, default: Any):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stage_update(
    round_row: AutoTrainingRound,
    *,
    stage: str,
    status: str,
    error: str | None = None,
) -> None:
    stages = _loads(round_row.stage_status_json, {})
    errors = _loads(round_row.stage_errors_json, {})
    stages[stage] = status
    if error:
        errors[stage] = error[:4000]
    else:
        errors.pop(stage, None)
    round_row.stage_status_json = _dumps(stages)
    round_row.stage_errors_json = _dumps(errors)


def _training_failure_limit(version: AutoTrainingPlanVersion) -> int:
    config = _loads(version.config_json, {})
    value = config.get("training_policy", {}).get("max_technical_failures", 3)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 3


def synchronize_training_job(
    db: Session, *, job_id: int, now: datetime | None = None
) -> LifecycleResult:
    current_time = now or datetime.now()
    job = db.query(YoloTrainJob).filter_by(id=job_id).one()
    if job.auto_training_round_id is None or job.auto_training_plan_id is None:
        raise ValueError("training job is not managed by auto training")
    round_row = (
        db.query(AutoTrainingRound)
        .filter_by(id=int(job.auto_training_round_id))
        .one()
    )
    plan = db.query(AutoTrainingPlan).filter_by(id=int(job.auto_training_plan_id)).one()
    version = (
        db.query(AutoTrainingPlanVersion)
        .filter_by(id=round_row.plan_version_id)
        .one()
    )

    if job.status == "PROCESSING":
        if round_row.state == RoundStatus.TRAINING.value:
            return LifecycleResult(False, "UNCHANGED", round_row.id)
        if round_row.state not in {RoundStatus.QUEUED.value, RoundStatus.SNAPSHOT_READY.value}:
            return LifecycleResult(False, "IGNORED_TERMINAL_STATE", round_row.id)
        round_row.state = RoundStatus.TRAINING.value
        round_row.started_at = round_row.started_at or current_time
        plan.status = "ROUND_ACTIVE"
        _stage_update(round_row, stage="TRAIN", status="PROCESSING")
        db.flush()
        return LifecycleResult(True, "TRAINING", round_row.id)

    if job.status == "COMPLETED":
        if round_row.state in {
            RoundStatus.PT_EVALUATING.value,
            RoundStatus.QUALITY_EVIDENCE_INSUFFICIENT.value,
            RoundStatus.QUALITY_FAILED.value,
            RoundStatus.AUTO_QUALITY_REACHED.value,
            RoundStatus.BM1688_QUEUED.value,
            RoundStatus.BM1688_COMPILING.value,
            RoundStatus.BMODEL_EVALUATING.value,
            RoundStatus.EDGE_VALIDATION_QUEUED.value,
            RoundStatus.EDGE_VALIDATING.value,
            RoundStatus.PUBLISH_READY.value,
            RoundStatus.PUBLISHED.value,
        }:
            return LifecycleResult(False, "UNCHANGED", round_row.id)
        if not str(job.weights_path or "").strip():
            job.status = "FAILED"
            job.error_message = "completed auto training job has no weights_path"
            return synchronize_training_job(db, job_id=job_id, now=current_time)
        round_row.state = RoundStatus.PT_EVALUATING.value
        round_row.started_at = round_row.started_at or current_time
        _stage_update(round_row, stage="TRAIN", status="COMPLETED")
        _stage_update(round_row, stage="PT_EVALUATION", status="QUEUED")
        enqueue_auto_training_event(
            db,
            event_name="auto.round.quality.evaluate.v1",
            aggregate_type="auto_training_round",
            aggregate_id=str(round_row.id),
            payload={
                "plan_id": plan.id,
                "round_id": round_row.id,
                "job_id": job.id,
            },
            idempotency_key=f"round:{round_row.id}:quality-evaluate",
        )
        db.flush()
        return LifecycleResult(True, "QUALITY_EVALUATION_QUEUED", round_row.id)

    if job.status == "FAILED":
        if round_row.state == RoundStatus.TRAIN_FAILED.value:
            return LifecycleResult(False, "UNCHANGED", round_row.id)
        retries = _loads(round_row.retry_count_json, {})
        failures = int(retries.get("TRAIN", 0)) + 1
        retries["TRAIN"] = failures
        round_row.retry_count_json = _dumps(retries)
        failure_limit = _training_failure_limit(version)
        if failures < failure_limit:
            job.status = "PENDING"
            job.error_message = None
            round_row.state = RoundStatus.QUEUED.value
            _stage_update(round_row, stage="TRAIN", status="RETRY_QUEUED")
            enqueue_auto_training_event(
                db,
                event_name="auto.round.train.dispatch.v1",
                aggregate_type="auto_training_round",
                aggregate_id=str(round_row.id),
                payload={
                    "plan_id": plan.id,
                    "round_id": round_row.id,
                    "job_id": job.id,
                    "retry_no": failures,
                },
                idempotency_key=f"round:{round_row.id}:train-dispatch:retry:{failures}",
            )
            db.flush()
            return LifecycleResult(True, "RETRY_QUEUED", round_row.id)
        round_row.state = RoundStatus.TRAIN_FAILED.value
        round_row.completed_at = current_time
        _stage_update(
            round_row,
            stage="TRAIN",
            status="FAILED",
            error=job.error_message or "training failed",
        )
        plan.status = "PAUSED_SAFETY"
        plan.status_reason_code = "TRAIN_TECHNICAL_FAILURE_LIMIT_REACHED"
        plan.status_reason_json = _dumps(
            {"round_id": round_row.id, "failures": failures, "limit": failure_limit}
        )
        plan.active_round_id = None
        plan.version += 1
        db.flush()
        return LifecycleResult(True, "SAFETY_PAUSED", round_row.id)

    if job.status == "CANCELLED" and round_row.state != RoundStatus.TRAIN_CANCELLED.value:
        round_row.state = RoundStatus.TRAIN_CANCELLED.value
        round_row.completed_at = current_time
        _stage_update(round_row, stage="TRAIN", status="CANCELLED")
        plan.active_round_id = None
        plan.status = "WAITING_FOR_DATA"
        plan.version += 1
        db.flush()
        return LifecycleResult(True, "CANCELLED", round_row.id)

    return LifecycleResult(False, "UNCHANGED", round_row.id)


def record_quality_evaluation(
    db: Session,
    *,
    round_id: int,
    overall_metrics: dict[str, Any],
    per_label_metrics: dict[str, dict[str, float]],
    evidence_manifest_rel_path: str | None,
    evidence_manifest_sha256: str | None,
    plate_component_metrics: dict[str, Any] | None = None,
    champion_comparison: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> LifecycleResult:
    current_time = now or datetime.now()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    version = (
        db.query(AutoTrainingPlanVersion)
        .filter_by(id=round_row.plan_version_id)
        .one()
    )
    if round_row.state != RoundStatus.PT_EVALUATING.value:
        existing = (
            db.query(AutoTrainingEvaluationRun).filter_by(round_id=round_id).first()
        )
        if existing and existing.status == "COMPLETED":
            return LifecycleResult(
                False, "UNCHANGED", round_id, decision_code=existing.decision_code
            )
        raise ValueError(f"round {round_id} is not waiting for PT evaluation")

    config = _loads(version.config_json, {})
    threshold = float(config.get("auto_stop", {}).get("overall_recall_threshold", 0.95))
    recall_value = overall_metrics.get("overall_recall")
    try:
        overall_recall = float(recall_value)
    except (TypeError, ValueError):
        overall_recall = 0.0
    evidence_complete = bool(
        evidence_manifest_rel_path
        and evidence_manifest_sha256
        and len(str(evidence_manifest_sha256)) == 64
        and recall_value is not None
    )
    decision = apply_quality_result(
        overall_recall=overall_recall,
        evidence_complete=evidence_complete,
        per_label_metrics=per_label_metrics,
        auto_stop_threshold=threshold,
    )
    binding = _loads(version.evaluation_binding_json, {})
    binding_signature = str(binding.get("signature") or version.config_sha256)
    evaluation = (
        db.query(AutoTrainingEvaluationRun).filter_by(round_id=round_id).first()
    )
    if evaluation is None:
        evaluation = AutoTrainingEvaluationRun(
            round_id=round_id,
            policy_signature=str(version.quality_policy_signature or version.config_sha256),
            evaluation_binding_signature=binding_signature,
            status="PENDING",
        )
        db.add(evaluation)
    evaluation.status = "COMPLETED"
    evaluation.overall_metrics_json = _dumps(overall_metrics)
    evaluation.per_label_metrics_json = _dumps(per_label_metrics)
    evaluation.plate_component_metrics_json = _dumps(plate_component_metrics or {})
    evaluation.champion_comparison_json = _dumps(champion_comparison or {})
    evaluation.evidence_manifest_rel_path = evidence_manifest_rel_path
    evaluation.evidence_manifest_sha256 = evidence_manifest_sha256
    evaluation.decision_code = decision.round_status.value
    evaluation.decision_json = _dumps(
        {
            "overall_recall_threshold": threshold,
            "warnings": decision.warnings,
            "blocking_reasons": (
                []
                if decision.round_status == RoundStatus.AUTO_QUALITY_REACHED
                else [decision.round_status.value]
            ),
        }
    )
    evaluation.started_at = evaluation.started_at or current_time
    evaluation.completed_at = current_time
    db.flush()
    round_row.evaluation_run_id = evaluation.id
    round_row.state = decision.round_status.value
    round_row.decision_code = decision.round_status.value
    round_row.decision_json = evaluation.decision_json
    _stage_update(round_row, stage="PT_EVALUATION", status="COMPLETED")

    if decision.round_status == RoundStatus.AUTO_QUALITY_REACHED:
        plan.status = "AUTO_QUALITY_REACHED"
        plan.status_reason_code = "OVERALL_RECALL_THRESHOLD_REACHED"
        plan.status_reason_json = _dumps(
            {"round_id": round_id, "overall_recall": overall_recall, "threshold": threshold}
        )
        enqueue_auto_training_event(
            db,
            event_name="auto.round.bm1688.requested.v1",
            aggregate_type="auto_training_round",
            aggregate_id=str(round_id),
            payload={
                "round_id": round_id,
                "origin": "AUTO_QUALITY",
                "reason": "overall recall reached automatic stop threshold",
            },
            idempotency_key=f"round:{round_id}:bm1688",
        )
    else:
        round_row.completed_at = current_time
        plan.active_round_id = None
        if decision.round_status == RoundStatus.QUALITY_FAILED:
            plan.status = "WAITING_FOR_DATA"
            plan.status_reason_code = "QUALITY_BELOW_THRESHOLD_WAITING_FOR_NEW_DATA"
        else:
            plan.status = "PAUSED_SAFETY"
            plan.status_reason_code = "QUALITY_EVIDENCE_INSUFFICIENT"
        plan.status_reason_json = evaluation.decision_json
        plan.version += 1
    db.flush()
    return LifecycleResult(
        True,
        "QUALITY_RECORDED",
        round_id,
        decision_code=decision.round_status.value,
    )


def record_quality_evaluation_from_job(
    db: Session, *, round_id: int, job_id: int
) -> LifecycleResult:
    job = db.query(YoloTrainJob).filter_by(id=job_id).one()
    if job.auto_training_round_id != round_id or job.status != "COMPLETED":
        raise ValueError("quality evaluation requires the completed round training job")
    existing = (
        db.query(AutoTrainingEvaluationRun).filter_by(round_id=round_id).first()
    )
    if existing is not None and existing.status == "COMPLETED":
        return LifecycleResult(
            False,
            "UNCHANGED",
            round_id,
            decision_code=existing.decision_code,
        )
    summary = _loads(job.metrics_summary, {})
    evidence = summary.get("auto_training_evaluation")
    if not isinstance(evidence, dict):
        raise OutboxDeferredError(
            "approved evaluation evidence is not ready", delay_seconds=300
        )
    overall_metrics = evidence.get("overall_metrics")
    per_label_metrics = evidence.get("per_label_metrics")
    if not isinstance(overall_metrics, dict) or not isinstance(per_label_metrics, dict):
        raise OutboxDeferredError(
            "approved evaluation metrics are incomplete", delay_seconds=300
        )
    return record_quality_evaluation(
        db,
        round_id=round_id,
        overall_metrics=overall_metrics,
        per_label_metrics=per_label_metrics,
        plate_component_metrics=evidence.get("plate_component_metrics")
        if isinstance(evidence.get("plate_component_metrics"), dict)
        else {},
        champion_comparison=evidence.get("champion_comparison")
        if isinstance(evidence.get("champion_comparison"), dict)
        else {},
        evidence_manifest_rel_path=evidence.get("evidence_manifest_rel_path"),
        evidence_manifest_sha256=evidence.get("evidence_manifest_sha256"),
    )
