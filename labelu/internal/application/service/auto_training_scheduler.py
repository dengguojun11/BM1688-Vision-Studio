from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_manifest import (
    persist_round_manifest,
    verify_round_manifest,
)
from labelu.internal.application.service.auto_training_plan import reserve_active_round
from labelu.internal.application.service.auto_training_state_machine import (
    build_execution_signature,
)
from labelu.internal.domain.models.annotation_contract import (
    TaskAnnotationContract,
    TaskAnnotationLabel,
)
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.auto_training import (
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
    AutoTrainingRoundSample,
    AutoTrainingSamplePool,
)
from labelu.internal.domain.models.auto_training_governance import (
    TrainingEvaluationSample,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


class AutoTrainingActiveRoundConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class TriggerPolicyDecision:
    should_create: bool
    reasons: tuple[str, ...]
    current_count: int
    previous_count: int
    new_or_changed_count: int
    growth_ratio: float


@dataclass(frozen=True)
class PlanTriggerResult:
    plan_id: int
    created: bool
    reasons: tuple[str, ...]
    round_id: int | None = None
    training_job_id: int | None = None


def _loads(raw: str | None, default: Any):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_for_signature(signature: str) -> str:
    bucket = int(signature[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "val"
    return "test"


def _covered_splits(
    pools: list[AutoTrainingSamplePool],
) -> dict[int, str]:
    assigned = {
        int(pool.sample_id): _split_for_signature(
            str(pool.sample_signature or _sha_text(f"sample:{pool.sample_id}"))
        )
        for pool in pools
    }
    counts = Counter(assigned.values())
    for target in ("val", "test"):
        if counts[target] > 0:
            continue
        donor = max(("train", "val", "test"), key=lambda name: counts[name])
        candidates = sorted(
            (
                str(pool.sample_signature or ""),
                int(pool.sample_id),
            )
            for pool in pools
            if assigned[int(pool.sample_id)] == donor
        )
        if len(candidates) <= 1:
            raise ValueError("not enough samples to create covered splits")
        sample_id = candidates[-1][1]
        assigned[sample_id] = target
        counts[donor] -= 1
        counts[target] += 1
    if counts["train"] <= 0:
        raise ValueError("frozen round has no training split")
    return assigned


def evaluate_trigger_policy(
    *,
    first_round: bool,
    current_count: int,
    previous_count: int,
    new_or_changed_count: int,
    min_new_samples: int,
    min_growth_ratio: float,
    next_evaluation_at: datetime | None,
    now: datetime,
) -> TriggerPolicyDecision:
    growth_ratio = (
        float(new_or_changed_count) / float(previous_count)
        if previous_count > 0
        else (1.0 if new_or_changed_count > 0 else 0.0)
    )
    if current_count <= 0:
        reasons = ("NO_QUALIFIED_TASK",)
        return TriggerPolicyDecision(
            False,
            reasons,
            current_count,
            previous_count,
            new_or_changed_count,
            growth_ratio,
        )
    if first_round:
        return TriggerPolicyDecision(
            True,
            ("FIRST_QUALIFIED_ROUND",),
            current_count,
            previous_count,
            new_or_changed_count,
            growth_ratio,
        )
    if new_or_changed_count <= 0:
        return TriggerPolicyDecision(
            False,
            ("NO_NEW_DATA",),
            current_count,
            previous_count,
            new_or_changed_count,
            growth_ratio,
        )
    reasons: list[str] = []
    if min_new_samples > 0 and new_or_changed_count >= min_new_samples:
        reasons.append("MIN_NEW_SAMPLES")
    if min_growth_ratio > 0 and growth_ratio >= min_growth_ratio:
        reasons.append("MIN_GROWTH_RATIO")
    if next_evaluation_at is not None and next_evaluation_at <= now:
        reasons.append("MAX_WAIT")
    return TriggerPolicyDecision(
        bool(reasons),
        tuple(reasons or ["TRIGGER_THRESHOLD_NOT_REACHED"]),
        current_count,
        previous_count,
        new_or_changed_count,
        growth_ratio,
    )


def _qualified_pools(
    db: Session, *, plan: AutoTrainingPlan, version: AutoTrainingPlanVersion
) -> list[AutoTrainingSamplePool]:
    config = _loads(version.config_json, {})
    minimum = int(
        config.get("data_admission", {}).get("min_eligible_samples_per_task", 50)
    )
    pools = (
        db.query(AutoTrainingSamplePool)
        .filter_by(plan_id=plan.id, pool_status="ELIGIBLE")
        .order_by(AutoTrainingSamplePool.task_id, AutoTrainingSamplePool.sample_id)
        .all()
    )
    evaluation = config.get("evaluation_binding", {})
    configured_dataset_ids = [
        evaluation.get("validation_dataset_id"),
        evaluation.get("benchmark_dataset_id"),
        evaluation.get("sealed_acceptance_dataset_id"),
        evaluation.get("edge_validation_dataset_id"),
        config.get("bm1688_policy", {}).get("calibration_dataset_id"),
    ]
    dataset_ids: list[int] = []
    for value in configured_dataset_ids:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            dataset_ids.append(parsed)
    if dataset_ids:
        reserved = {
            int(sample_id)
            for (sample_id,) in db.query(TrainingEvaluationSample.sample_id)
            .filter(TrainingEvaluationSample.evaluation_dataset_id.in_(dataset_ids))
            .all()
        }
        pools = [row for row in pools if int(row.sample_id) not in reserved]
    counts = Counter(row.task_id for row in pools)
    return [row for row in pools if counts[row.task_id] >= minimum]


def evaluate_plan_trigger(
    db: Session, *, plan_id: int, now: datetime | None = None
) -> PlanTriggerResult:
    current_time = now or datetime.now()
    # Serialize round creation for one plan. MySQL obtains a row lock; SQLite
    # serializes the following write transaction. The duplicate signature
    # lookup is therefore authoritative after the lock is acquired.
    plan = (
        db.query(AutoTrainingPlan)
        .filter_by(id=plan_id)
        .with_for_update()
        .one()
    )
    if plan.status == "AUTO_QUALITY_REACHED":
        return PlanTriggerResult(plan.id, False, ("AUTO_QUALITY_REACHED",))
    if plan.active_round_id is not None:
        return PlanTriggerResult(plan.id, False, ("ACTIVE_ROUND_EXISTS",))
    if plan.status not in {
        "ACTIVE",
        "WAITING_FOR_DATA",
        "WAITING_FOR_REVIEW",
        "WAITING_FOR_COVERAGE",
    }:
        return PlanTriggerResult(plan.id, False, ("PLAN_NOT_AUTOMATABLE",))
    version = db.query(AutoTrainingPlanVersion).filter_by(id=plan.current_version_id).one()
    config = _loads(version.config_json, {})
    included = _qualified_pools(db, plan=plan, version=version)
    current_signatures = {
        str(row.sample_signature or _sha_text(f"sample:{row.sample_id}"))
        for row in included
    }
    previous = (
        db.query(AutoTrainingRound)
        .filter_by(plan_id=plan.id)
        .order_by(AutoTrainingRound.round_no.desc(), AutoTrainingRound.id.desc())
        .first()
    )
    previous_signatures: set[str] = set()
    if previous is not None:
        previous_signatures = {
            str(value)
            for (value,) in db.query(AutoTrainingRoundSample.sample_signature)
            .filter_by(round_id=previous.id)
            .all()
        }
    trigger = config.get("trigger_policy", {})
    decision = evaluate_trigger_policy(
        first_round=previous is None,
        current_count=len(current_signatures),
        previous_count=len(previous_signatures),
        new_or_changed_count=len(current_signatures - previous_signatures),
        min_new_samples=int(trigger.get("min_new_samples") or 0),
        min_growth_ratio=float(trigger.get("min_growth_ratio") or 0),
        next_evaluation_at=plan.next_evaluation_at,
        now=current_time,
    )
    if not decision.should_create:
        return PlanTriggerResult(plan.id, False, decision.reasons)
    round_row = create_frozen_round(
        db,
        plan_id=plan.id,
        trigger_type="AUTO",
        trigger_snapshot={
            "reasons": list(decision.reasons),
            "current_count": decision.current_count,
            "previous_count": decision.previous_count,
            "new_or_changed_count": decision.new_or_changed_count,
            "growth_ratio": decision.growth_ratio,
        },
        expected_plan_version=plan.version,
    )
    _training_task, job = materialize_training_job(db, round_id=round_row.id)
    plan.next_evaluation_at = None
    db.flush()
    return PlanTriggerResult(
        plan.id,
        True,
        decision.reasons,
        round_id=round_row.id,
        training_job_id=job.id,
    )


def evaluate_due_plans(
    db: Session, *, now: datetime | None = None, limit: int = 20
) -> list[PlanTriggerResult]:
    current_time = now or datetime.now()
    plan_ids = [
        int(plan_id)
        for (plan_id,) in (
            db.query(AutoTrainingPlan.id)
            .filter(
                AutoTrainingPlan.deleted_at.is_(None),
                AutoTrainingPlan.active_round_id.is_(None),
                AutoTrainingPlan.status.in_(
                    (
                        "ACTIVE",
                        "WAITING_FOR_DATA",
                        "WAITING_FOR_REVIEW",
                        "WAITING_FOR_COVERAGE",
                    )
                ),
                AutoTrainingPlan.next_evaluation_at.isnot(None),
                AutoTrainingPlan.next_evaluation_at <= current_time,
            )
            .order_by(AutoTrainingPlan.next_evaluation_at, AutoTrainingPlan.id)
            .limit(max(1, int(limit)))
            .all()
        )
    ]
    return [
        evaluate_plan_trigger(db, plan_id=plan_id, now=current_time)
        for plan_id in plan_ids
    ]


def _round_source_manifest(db: Session, pool: AutoTrainingSamplePool, split: str) -> dict[str, Any]:
    task = db.query(Task).filter_by(id=pool.task_id).one()
    sample = db.query(TaskSample).filter_by(id=pool.sample_id).one()
    contract_id = task.current_annotation_contract_id
    source_path = sample.file.path if sample.file else None
    annotation_snapshot = _loads(sample.data, {})
    return {
        "source_task_id": int(pool.task_id),
        "source_sample_id": int(pool.sample_id),
        "contract_id": int(contract_id or 0),
        "annotation_sha256": pool.annotation_sha256,
        "image_sha256": pool.image_sha256,
        "sample_signature": pool.sample_signature,
        "source_path": source_path,
        "source_filename": sample.file.filename if sample.file else None,
        "annotation_snapshot": annotation_snapshot,
        "split": split,
    }


def create_frozen_round(
    db: Session,
    *,
    plan_id: int,
    trigger_type: str,
    trigger_snapshot: dict[str, Any] | None = None,
    expected_plan_version: int | None = None,
) -> AutoTrainingRound:
    plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
    version = db.query(AutoTrainingPlanVersion).filter_by(id=plan.current_version_id).one()
    included = _qualified_pools(db, plan=plan, version=version)
    if not included:
        raise ValueError("no qualified task samples")
    dataset_signature = _sha_text(
        "|".join(sorted(str(row.sample_signature or "") for row in included))
    )
    execution_signature = build_execution_signature(
        plan_id=plan.id,
        dataset_signature=dataset_signature,
        baseline_signature=str(plan.champion_job_id or "none"),
        recipe_signature=_sha_text(version.training_recipe_snapshot_json or "{}"),
        hyperparams_signature=version.config_sha256,
        quality_policy_signature=version.quality_policy_signature or "",
        runtime_signature=plan.series_type,
    )
    duplicate = (
        db.query(AutoTrainingRound)
        .filter_by(execution_signature=execution_signature)
        .first()
    )
    if duplicate is not None:
        return duplicate
    if plan.active_round_id is not None:
        raise ValueError("plan already has an active round")
    round_no = (
        db.query(AutoTrainingRound)
        .filter_by(plan_id=plan.id)
        .count()
        + 1
    )
    round_row = AutoTrainingRound(
        plan_id=plan.id,
        plan_version_id=version.id,
        round_no=round_no,
        state="SNAPSHOT_READY",
        trigger_type=trigger_type,
        trigger_snapshot_json=json.dumps(trigger_snapshot or {}, ensure_ascii=False),
        dataset_signature=dataset_signature,
        execution_signature=execution_signature,
        eligible_sample_count=len(included),
        stage_status_json=json.dumps({"SNAPSHOT": "COMPLETED"}),
        stage_errors_json="{}",
        retry_count_json="{}",
    )
    db.add(round_row)
    db.flush()
    manifest_samples: list[dict[str, Any]] = []
    split_counts = Counter()
    assigned_splits = _covered_splits(included)
    for pool in included:
        signature = pool.sample_signature or _sha_text(f"sample:{pool.sample_id}")
        split = assigned_splits[int(pool.sample_id)]
        source_manifest = _round_source_manifest(db, pool, split)
        db.add(
            AutoTrainingRoundSample(
                round_id=round_row.id,
                task_id=pool.task_id,
                sample_id=pool.sample_id,
                split=split,
                sample_signature=signature,
                annotation_sha256=pool.annotation_sha256 or "0" * 64,
                image_sha256=pool.image_sha256 or "0" * 64,
                source_sample_manifest_json=json.dumps(
                    source_manifest, ensure_ascii=False, sort_keys=True
                ),
            )
        )
        manifest_samples.append(source_manifest)
        split_counts[split] += 1
    db.flush()
    manifest_payload = {
        "schema_version": 1,
        "plan_id": plan.id,
        "plan_version_id": version.id,
        "round_id": round_row.id,
        "round_no": round_row.round_no,
        "series_type": plan.series_type,
        "dataset_signature": dataset_signature,
        "label_set_signature": plan.label_set_signature,
        "class_order_signature": plan.class_order_signature,
        "samples": manifest_samples,
    }
    round_row.train_count = split_counts["train"]
    round_row.val_count = split_counts["val"]
    round_row.test_count = split_counts["test"]
    if not reserve_active_round(
        db,
        plan_id=plan.id,
        round_id=round_row.id,
        expected_plan_version=(
            plan.version if expected_plan_version is None else expected_plan_version
        ),
    ):
        raise AutoTrainingActiveRoundConflict(
            "active round compare-and-swap conflict"
        )
    rel_path, manifest_sha = persist_round_manifest(round_row, payload=manifest_payload)
    round_row.manifest_rel_path = rel_path
    round_row.manifest_sha256 = manifest_sha
    db.flush()
    return round_row


def _label_snapshot(db: Session, plan: AutoTrainingPlan, task_id: int) -> tuple[list[int], list[str]]:
    task = db.query(Task).filter_by(id=task_id).one()
    contract = (
        db.query(TaskAnnotationContract)
        .filter_by(id=task.current_annotation_contract_id)
        .one()
    )
    links = (
        db.query(TaskAnnotationLabel, AnnotationLabel)
        .join(AnnotationLabel, AnnotationLabel.id == TaskAnnotationLabel.label_id)
        .filter(
            TaskAnnotationLabel.contract_id == contract.id,
            TaskAnnotationLabel.status == "active",
        )
        .order_by(TaskAnnotationLabel.sort_order, TaskAnnotationLabel.id)
        .all()
    )
    if links:
        return (
            [int(label.id) for _link, label in links],
            [str(label.label_value or label.label_key) for _link, label in links],
        )
    label_ids = [int(value) for value in _loads(contract.label_ids_json, [])]
    snapshot = _loads(contract.labels_snapshot_json, [])
    names = [
        str(item.get("label_value") or item.get("label_key") or f"class_{index + 1}")
        for index, item in enumerate(snapshot)
    ]
    if len(names) != len(label_ids):
        names = [f"class_{label_id}" for label_id in label_ids]
    return label_ids, names


def materialize_training_job(
    db: Session, *, round_id: int
) -> tuple[TrainingTask, YoloTrainJob]:
    # Lock the round before the idempotency lookup so concurrent materializers
    # cannot both create a TrainingTask/YoloTrainJob pair for the same round.
    round_row = (
        db.query(AutoTrainingRound)
        .filter_by(id=round_id)
        .with_for_update()
        .one()
    )
    existing = db.query(YoloTrainJob).filter_by(auto_training_round_id=round_id).first()
    if existing is not None:
        training_task = db.query(TrainingTask).filter_by(id=existing.training_task_id).one()
        return training_task, existing
    if round_row.state != "SNAPSHOT_READY":
        raise ValueError(f"round {round_id} is not snapshot-ready")
    verify_round_manifest(round_row)
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    version = db.query(AutoTrainingPlanVersion).filter_by(id=round_row.plan_version_id).one()
    config = _loads(version.config_json, {})
    samples = (
        db.query(AutoTrainingRoundSample)
        .filter_by(round_id=round_id)
        .order_by(AutoTrainingRoundSample.task_id, AutoTrainingRoundSample.sample_id)
        .all()
    )
    if not samples:
        raise ValueError("round has no frozen samples")
    primary_task_id = int(samples[0].task_id)
    source_task_ids = list(dict.fromkeys(int(row.task_id) for row in samples))
    selected_label_ids, class_names = _label_snapshot(db, plan, primary_task_id)
    source_manifest = [_loads(row.source_sample_manifest_json, {}) for row in samples]
    source_tasks = [
        {"task_id": task_id, "included_image_count": sum(1 for row in samples if row.task_id == task_id)}
        for task_id in source_task_ids
    ]
    presets = []
    if plan.series_type in {"plate_detection", "plate_recognition"}:
        presets = [
            {
                "preset_slug": "plate_recognition",
                "training_profile": "plate_recognition_v1",
                "training_profile_config": config.get("plate_profile", {}),
            }
        ]
    quality_snapshot = config.get("quality_policy_binding", {})
    training_task = TrainingTask(
        owner_id=plan.owner_id,
        name=f"{plan.name}-round-{round_row.round_no}"[:128],
        mode=plan.series_type,
        status="PENDING",
        primary_task_id=primary_task_id,
        selected_presets_snapshot_json=json.dumps(presets, ensure_ascii=False),
        selected_label_items_snapshot_json="[]",
        merged_class_names_snapshot_json=json.dumps(class_names, ensure_ascii=False),
        source_tasks_snapshot_json=json.dumps(source_tasks, ensure_ascii=False),
        ignored_tasks_snapshot_json="[]",
        preview_summary_json=json.dumps(
            {"included_task_count": len(source_task_ids), "included_image_count": len(samples)},
            ensure_ascii=False,
        ),
        dataset_category_id=plan.dataset_category_id,
        selected_label_ids_json=json.dumps(selected_label_ids),
        class_names_snapshot_json=json.dumps(class_names, ensure_ascii=False),
        label_set_signature=plan.label_set_signature,
        class_order_signature=plan.class_order_signature,
        source_sample_snapshot_json=json.dumps(source_manifest, ensure_ascii=False),
        quality_policy_snapshot_json=json.dumps(quality_snapshot, ensure_ascii=False),
        auto_training_plan_id=plan.id,
        auto_training_round_id=round_row.id,
        source_mode="AUTO",
    )
    db.add(training_task)
    db.flush()
    training_policy = config.get("training_policy", {})
    weights = str(
        training_policy.get("model_weights")
        or ("yolov8n-pose.pt" if plan.series_type != "yolo_detection" else "yolo11n.pt")
    )
    train_signature = _sha_text(f"{round_row.execution_signature}:train")
    primary_task = db.query(Task).filter(Task.id == primary_task_id).one_or_none()
    job = YoloTrainJob(
        training_task_id=training_task.id,
        task_id=primary_task_id,
        project_id=(int(primary_task.project_id) if primary_task and primary_task.project_id else None),
        created_by=plan.owner_id,
        source_type="auto_training",
        source_system="labelu_auto_training",
        status="PENDING",
        job_type=plan.series_type,
        model_weights=weights,
        epochs=int(training_policy.get("epochs", 50)),
        imgsz=int(training_policy.get("imgsz", 640)),
        category=training_task.name,
        hyperparams_json=json.dumps(training_policy.get("hyperparams", {}), ensure_ascii=False),
        source_task_ids_json=json.dumps(source_task_ids),
        dataset_manifest_json=json.dumps(
            {"manifest_rel_path": round_row.manifest_rel_path, "manifest_sha256": round_row.manifest_sha256},
            ensure_ascii=False,
        ),
        dataset_category_id=plan.dataset_category_id,
        class_names_json=json.dumps(class_names, ensure_ascii=False),
        label_set_signature=plan.label_set_signature,
        class_order_signature=plan.class_order_signature,
        dataset_content_signature=round_row.dataset_signature,
        source_sample_manifest_json=json.dumps(source_manifest, ensure_ascii=False),
        quality_policy_snapshot_json=json.dumps(quality_snapshot, ensure_ascii=False),
        auto_training_plan_id=plan.id,
        auto_training_round_id=round_row.id,
        queue_priority=1000 + int(training_policy.get("priority", 100)),
        queue_source="AUTO",
        execution_signature=train_signature,
        execution_mode="local",
    )
    db.add(job)
    db.flush()
    training_task.latest_job_id = job.id
    training_task.latest_job_status = job.status
    round_row.training_task_id = training_task.id
    round_row.yolo_train_job_id = job.id
    round_row.state = "QUEUED"
    round_row.queued_at = datetime.now()
    round_row.stage_status_json = json.dumps(
        {"SNAPSHOT": "COMPLETED", "TRAIN": "QUEUED"}, ensure_ascii=False
    )
    enqueue_auto_training_event(
        db,
        event_name="auto.round.train.dispatch.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(round_row.id),
        payload={"plan_id": plan.id, "round_id": round_row.id, "job_id": job.id},
        idempotency_key=f"round:{round_row.id}:train-dispatch",
    )
    db.flush()
    return training_task, job
