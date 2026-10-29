"""Local-only evaluator for exercising the trusted auto-training loop.

This module deliberately does not run inference.  It consumes an immutable
``evaluation-request.json`` and a caller-supplied metric fixture (or a
conservative zero-recall fixture), writes a content-addressed evidence
manifest, and then delegates the state transition to the normal lifecycle
service.  Every generated artifact is marked ``local_simulation`` so it
cannot be mistaken for production evidence.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_evaluator import (
    claim_evaluation_run,
)
from labelu.internal.application.service.auto_training_lifecycle import (
    record_quality_evaluation_from_job,
)
from labelu.internal.application.service.auto_training_state_machine import (
    apply_quality_result,
)
from labelu.internal.common.config import settings
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


class LocalEvaluationInputError(ValueError):
    """Raised when a local evaluator input cannot be trusted."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _safe_relative_path(value: str, *, field: str) -> Path:
    normalized = str(value or "").strip().replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise LocalEvaluationInputError(f"{field} must be a relative path")
    return path


def _media_path(relative: str, *, field: str) -> Path:
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / _safe_relative_path(relative, field=field)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LocalEvaluationInputError(f"{field} escapes MEDIA_ROOT") from exc
    return path


def _read_verified_request(
    row: AutoTrainingEvaluationRun,
) -> tuple[dict[str, Any], bytes]:
    if not row.request_manifest_rel_path or not row.request_manifest_sha256:
        raise LocalEvaluationInputError("evaluation request manifest is incomplete")
    target = _media_path(row.request_manifest_rel_path, field="request manifest path")
    if not target.is_file():
        raise LocalEvaluationInputError("evaluation request manifest is missing")
    content = target.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != str(row.request_manifest_sha256).lower():
        raise LocalEvaluationInputError(
            "evaluation request manifest sha256 does not match"
        )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LocalEvaluationInputError(
            "evaluation request manifest is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise LocalEvaluationInputError("evaluation request manifest must be an object")
    try:
        request_round_id = int(payload.get("round_id", -1))
    except (TypeError, ValueError) as exc:
        raise LocalEvaluationInputError(
            "evaluation request round_id is invalid"
        ) from exc
    if request_round_id != int(row.round_id):
        raise LocalEvaluationInputError(
            "evaluation request round lineage does not match"
        )
    return payload, content


def _bounded_metric(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise LocalEvaluationInputError(f"{name} must be a number between 0 and 1")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LocalEvaluationInputError(
            f"{name} must be a number between 0 and 1"
        ) from exc
    if not 0 <= parsed <= 1:
        raise LocalEvaluationInputError(f"{name} must be a number between 0 and 1")
    return parsed


def _normalise_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a fixture without implying that it came from real inference."""
    source = metrics or {}
    if not isinstance(source, dict):
        raise LocalEvaluationInputError("metrics fixture must be a JSON object")
    overall = source.get("overall_metrics") or {"overall_recall": 0.0}
    labels = source.get("per_label_metrics") or {}
    if not isinstance(overall, dict) or not isinstance(labels, dict):
        raise LocalEvaluationInputError("metrics fixture has invalid metric objects")
    if "overall_recall" not in overall:
        raise LocalEvaluationInputError("metrics fixture requires overall_recall")
    normal_overall = dict(overall)
    normal_overall["overall_recall"] = _bounded_metric(
        normal_overall["overall_recall"], name="overall_recall"
    )
    normal_labels: dict[str, dict[str, Any]] = {}
    for label, raw in labels.items():
        if not str(label).strip() or not isinstance(raw, dict):
            raise LocalEvaluationInputError(
                "per_label_metrics must be objects keyed by label"
            )
        item = dict(raw)
        if "recall" in item:
            item["recall"] = _bounded_metric(item["recall"], name=f"{label}.recall")
        else:
            item["recall"] = 0.0
        for key in ("precision", "f1", "map50", "map50_95"):
            if key in item and item[key] is not None:
                item[key] = _bounded_metric(item[key], name=f"{label}.{key}")
        normal_labels[str(label)] = item
    return {
        "overall_metrics": normal_overall,
        "per_label_metrics": normal_labels,
        "plate_component_metrics": (
            source.get("plate_component_metrics")
            if isinstance(source.get("plate_component_metrics"), dict)
            else {}
        ),
        "champion_comparison": (
            source.get("champion_comparison")
            if isinstance(source.get("champion_comparison"), dict)
            else {}
        ),
    }


def _auto_stop_threshold(db: Session, *, round_id: int) -> float:
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    version = (
        db.query(AutoTrainingPlanVersion).filter_by(id=round_row.plan_version_id).one()
    )
    config = _loads(version.config_json, {})
    try:
        return _bounded_metric(
            config.get("auto_stop", {}).get("overall_recall_threshold", 0.95),
            name="overall_recall_threshold",
        )
    except LocalEvaluationInputError:
        return 0.95


def _write_atomic(relative: str, content: bytes) -> str:
    target = _media_path(relative, field="evidence manifest path")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)
    return hashlib.sha256(content).hexdigest()


def run_local_simulated_evaluation(
    db: Session,
    *,
    evaluation_run_id: int,
    worker_id: str = "local-simulation-evaluator",
    lease_seconds: int = 300,
    metrics: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one local simulation and use the normal lifecycle write-back.

    The caller owns the transaction.  No production inference is performed;
    metrics are either supplied as a local fixture or default to zero recall.
    """
    row = (
        db.query(AutoTrainingEvaluationRun)
        .filter_by(id=evaluation_run_id)
        .one_or_none()
    )
    if row is None:
        raise LocalEvaluationInputError("evaluation run was not found")
    if row.status == "COMPLETED":
        return {
            "evaluation_run_id": row.id,
            "status": row.status,
            "decision_code": row.decision_code,
            "changed": False,
            "local_simulation": True,
        }
    claimed = claim_evaluation_run(
        db,
        run_id=evaluation_run_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=now,
    )
    request, _request_content = _read_verified_request(claimed)
    round_row = db.query(AutoTrainingRound).filter_by(id=claimed.round_id).one()
    try:
        request_plan_id = int(request.get("plan_id", -1))
    except (TypeError, ValueError) as exc:
        raise LocalEvaluationInputError(
            "evaluation request plan_id is invalid"
        ) from exc
    if request_plan_id != int(round_row.plan_id):
        raise LocalEvaluationInputError(
            "evaluation request plan lineage does not match"
        )
    job_id = request.get("training_job_id")
    if not isinstance(job_id, int) or job_id < 1:
        raise LocalEvaluationInputError("evaluation request training_job_id is invalid")
    job = (
        db.query(YoloTrainJob)
        .filter_by(
            id=job_id, auto_training_round_id=claimed.round_id, status="COMPLETED"
        )
        .one_or_none()
    )
    if job is None:
        raise LocalEvaluationInputError(
            "evaluation request training job is not completed"
        )

    metric_payload = _normalise_metrics(metrics)
    threshold = _auto_stop_threshold(db, round_id=claimed.round_id)
    quality = apply_quality_result(
        overall_recall=float(metric_payload["overall_metrics"]["overall_recall"]),
        evidence_complete=True,
        per_label_metrics=metric_payload["per_label_metrics"],
        auto_stop_threshold=threshold,
    )
    generated_at = (now or datetime.now()).isoformat()
    evidence_rel = (
        Path("auto_training")
        / f"plan-{round_row.plan_id}"
        / f"round-{claimed.round_id}"
        / "local-simulation-evidence.json"
    ).as_posix()
    evidence = {
        "format": "labelu_auto_training_evidence_v1",
        "local_simulation": True,
        "metrics_source": "local_fixture" if metrics else "deterministic_zero_recall",
        "generated_at": generated_at,
        "evaluation_run_id": int(claimed.id),
        "round_id": int(claimed.round_id),
        "training_job_id": int(job.id),
        "evaluation_request_sha256": str(claimed.request_manifest_sha256),
        "dataset_count": len(request.get("datasets") or []),
        "metrics": metric_payload,
        "decision_preview": {
            "code": quality.round_status.value,
            "overall_recall_threshold": threshold,
            "warnings": quality.warnings,
        },
    }
    evidence_bytes = _canonical_bytes(evidence)
    evidence_sha256 = _write_atomic(evidence_rel, evidence_bytes)

    summary = _loads(job.metrics_summary, {})
    if not isinstance(summary, dict):
        summary = {}
    summary["auto_training_evaluation"] = {
        **metric_payload,
        "evidence_manifest_rel_path": evidence_rel,
        "evidence_manifest_sha256": evidence_sha256,
        "evaluation_run_id": int(claimed.id),
        "evaluation_request_sha256": str(claimed.request_manifest_sha256),
        "local_simulation": True,
        "metrics_source": evidence["metrics_source"],
    }
    job.metrics_summary = json.dumps(
        summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    result = record_quality_evaluation_from_job(
        db, round_id=claimed.round_id, job_id=job.id
    )
    if result.decision_code != quality.round_status.value:
        raise LocalEvaluationInputError(
            "local simulation decision changed during lifecycle write-back"
        )
    return {
        "evaluation_run_id": int(claimed.id),
        "round_id": int(claimed.round_id),
        "training_job_id": int(job.id),
        "status": "COMPLETED",
        "changed": result.changed,
        "decision_code": result.decision_code,
        "evidence_manifest_rel_path": evidence_rel,
        "evidence_manifest_sha256": evidence_sha256,
        "local_simulation": True,
    }


__all__ = ["LocalEvaluationInputError", "run_local_simulated_evaluation"]
