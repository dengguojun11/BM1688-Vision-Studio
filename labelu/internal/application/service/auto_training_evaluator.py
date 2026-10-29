from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_pipeline_runtime import (
    _validate_evaluation_samples,
)
from labelu.internal.common.config import settings
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.auto_training_governance import (
    TrainingEvaluationDataset,
    TrainingEvaluationSample,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


REQUIRED_DATASETS = (
    ("validation_dataset_id", "VALIDATION"),
    ("benchmark_dataset_id", "BENCHMARK"),
    ("sealed_acceptance_dataset_id", "SEALED_ACCEPTANCE"),
)
EVALUATION_RUN_STATUSES = frozenset({"PENDING", "PROCESSING", "COMPLETED", "FAILED"})


class EvaluationRunNotFoundError(ValueError):
    pass


class EvaluationRunConflictError(ValueError):
    pass


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _verify_dataset_manifest(dataset: TrainingEvaluationDataset) -> None:
    target = Path(settings.MEDIA_ROOT) / dataset.manifest_rel_path
    if not target.is_file():
        raise ValueError(f"approved evaluation dataset #{dataset.id} manifest is missing")
    if hashlib.sha256(target.read_bytes()).hexdigest() != dataset.manifest_sha256:
        raise ValueError(
            f"approved evaluation dataset #{dataset.id} manifest hash changed"
        )


def _dataset_request_payload(
    db: Session,
    *,
    dataset: TrainingEvaluationDataset,
    expected_kind: str,
) -> dict[str, Any]:
    if dataset.status != "APPROVED" or dataset.dataset_kind != expected_kind:
        raise ValueError(f"{expected_kind} evaluation dataset is not approved")
    _verify_dataset_manifest(dataset)
    rows = (
        db.query(TrainingEvaluationSample)
        .filter_by(evaluation_dataset_id=dataset.id)
        .order_by(TrainingEvaluationSample.id)
        .all()
    )
    if not rows or len(rows) != int(dataset.sample_count or 0):
        raise ValueError(
            f"approved evaluation dataset #{dataset.id} sample count is inconsistent"
        )
    sample_task_ids = _validate_evaluation_samples(db, rows=rows)
    return {
        "dataset_id": int(dataset.id),
        "dataset_kind": dataset.dataset_kind,
        "manifest_rel_path": dataset.manifest_rel_path,
        "manifest_sha256": dataset.manifest_sha256,
        "sample_count": len(rows),
        "samples": [
            {
                "task_id": int(item.task_id),
                "sample_id": int(item.sample_id),
                "split_role": item.split_role,
                "sample_signature": item.sample_signature,
                "annotation_sha256": item.annotation_sha256,
                "image_sha256": item.image_sha256,
                "identity_group_key": item.identity_group_key,
                "capture_group_key": item.capture_group_key,
            }
            for item in rows
        ],
        "task_sample_groups": {
            str(task_id): sorted(
                sample_id
                for sample_id, mapped_task_id in sample_task_ids.items()
                if mapped_task_id == task_id
            )
            for task_id in sorted(set(sample_task_ids.values()))
        },
    }


def materialize_evaluation_request(
    db: Session,
    *,
    round_id: int,
    job_id: int,
) -> AutoTrainingEvaluationRun:
    from labelu.internal.application.service.auto_training_plan import (
        ensure_auto_training_plan_writes_open,
    )

    ensure_auto_training_plan_writes_open()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    version = (
        db.query(AutoTrainingPlanVersion).filter_by(id=round_row.plan_version_id).one()
    )
    job = db.query(YoloTrainJob).filter_by(id=job_id).one()
    if round_row.state != "PT_EVALUATING":
        raise ValueError("round is not waiting for trusted evaluation")
    if (
        round_row.yolo_train_job_id != job.id
        or job.auto_training_round_id != round_row.id
        or job.auto_training_plan_id != plan.id
        or job.status != "COMPLETED"
        or not str(job.weights_path or "").strip()
    ):
        raise ValueError("trusted evaluation training job lineage is invalid")

    existing = (
        db.query(AutoTrainingEvaluationRun).filter_by(round_id=round_id).first()
    )
    if existing is not None:
        if existing.status in {"PENDING", "PROCESSING", "COMPLETED"}:
            return existing
        raise ValueError("trusted evaluation request is in an unrecoverable state")

    config = _loads(version.config_json)
    binding = config.get("evaluation_binding")
    if not isinstance(binding, dict):
        raise ValueError("approved evaluation binding is missing")
    datasets: list[dict[str, Any]] = []
    for binding_key, expected_kind in REQUIRED_DATASETS:
        dataset_id = binding.get(binding_key)
        if not dataset_id:
            raise ValueError(f"approved evaluation binding is missing {binding_key}")
        dataset = (
            db.query(TrainingEvaluationDataset)
            .filter_by(id=int(dataset_id))
            .one_or_none()
        )
        if dataset is None:
            raise ValueError(f"approved evaluation dataset #{dataset_id} was not found")
        if (
            dataset.series_type != plan.series_type
            or int(dataset.dataset_category_id) != int(plan.dataset_category_id)
            or dataset.label_set_signature != plan.label_set_signature
        ):
            raise ValueError(
                f"approved evaluation dataset #{dataset.id} scope does not match plan"
            )
        datasets.append(
            _dataset_request_payload(
                db, dataset=dataset, expected_kind=expected_kind
            )
        )

    payload = {
        "format": "labelu_auto_training_evaluation_request_v1",
        "plan_id": int(plan.id),
        "plan_version_id": int(version.id),
        "round_id": int(round_row.id),
        "round_dataset_signature": round_row.dataset_signature,
        "training_job_id": int(job.id),
        "weights_path": str(job.weights_path),
        "series_type": plan.series_type,
        "dataset_category_id": int(plan.dataset_category_id),
        "label_set_signature": plan.label_set_signature,
        "class_order_signature": plan.class_order_signature,
        "quality_policy_signature": str(
            version.quality_policy_signature or version.config_sha256
        ),
        "evaluation_binding_signature": str(
            binding.get("signature") or version.config_sha256
        ),
        "datasets": datasets,
    }
    content = _canonical_bytes(payload)
    relative = (
        Path("auto_training")
        / f"plan-{plan.id}"
        / f"round-{round_row.id}"
        / "evaluation-request.json"
    )
    target = Path(settings.MEDIA_ROOT) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)

    evaluation = AutoTrainingEvaluationRun(
        round_id=round_row.id,
        policy_signature=payload["quality_policy_signature"],
        evaluation_binding_signature=payload["evaluation_binding_signature"],
        status="PENDING",
        request_manifest_rel_path=relative.as_posix(),
        request_manifest_sha256=hashlib.sha256(content).hexdigest(),
    )
    db.add(evaluation)
    db.flush()
    round_row.evaluation_run_id = evaluation.id
    db.flush()
    return evaluation


def list_evaluation_runs(
    db: Session,
    *,
    statuses: set[str],
    limit: int,
) -> list[AutoTrainingEvaluationRun]:
    if (
        not statuses
        or not statuses <= EVALUATION_RUN_STATUSES
        or limit < 1
        or limit > 100
    ):
        raise ValueError("statuses and a limit between 1 and 100 are required")
    return (
        db.query(AutoTrainingEvaluationRun)
        .filter(AutoTrainingEvaluationRun.status.in_(sorted(statuses)))
        .order_by(AutoTrainingEvaluationRun.id)
        .limit(limit)
        .all()
    )


def claim_evaluation_run(
    db: Session,
    *,
    run_id: int,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> AutoTrainingEvaluationRun:
    from labelu.internal.application.service.auto_training_plan import (
        ensure_auto_training_plan_writes_open,
    )

    ensure_auto_training_plan_writes_open()
    current = now or datetime.now()
    worker = str(worker_id or "").strip()
    if not worker:
        raise ValueError("worker_id is required")
    if lease_seconds < 30 or lease_seconds > 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    row = db.query(AutoTrainingEvaluationRun).filter_by(id=run_id).one_or_none()
    if row is None:
        raise EvaluationRunNotFoundError("evaluation run was not found")
    if row.status == "COMPLETED":
        return row
    active_other_lease = (
        row.status == "PROCESSING"
        and row.evaluator_worker_id
        and row.evaluator_worker_id != worker
        and row.evaluator_lease_expires_at is not None
        and row.evaluator_lease_expires_at > current
    )
    if active_other_lease:
        raise EvaluationRunConflictError("evaluation run is leased by another worker")
    if row.status not in {"PENDING", "PROCESSING"}:
        raise EvaluationRunConflictError(
            "evaluation run cannot be claimed in its current state"
        )
    row.status = "PROCESSING"
    row.evaluator_worker_id = worker
    row.evaluator_claimed_at = row.evaluator_claimed_at or current
    row.evaluator_lease_expires_at = current + timedelta(seconds=lease_seconds)
    row.started_at = row.started_at or current
    db.flush()
    return row


def evaluation_run_payload(row: AutoTrainingEvaluationRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "round_id": row.round_id,
        "status": row.status,
        "policy_signature": row.policy_signature,
        "evaluation_binding_signature": row.evaluation_binding_signature,
        "request_manifest_rel_path": row.request_manifest_rel_path,
        "request_manifest_sha256": row.request_manifest_sha256,
        "evaluator_worker_id": row.evaluator_worker_id,
        "evaluator_claimed_at": row.evaluator_claimed_at,
        "evaluator_lease_expires_at": row.evaluator_lease_expires_at,
        "decision_code": row.decision_code,
        "error_message": row.error_message,
    }
