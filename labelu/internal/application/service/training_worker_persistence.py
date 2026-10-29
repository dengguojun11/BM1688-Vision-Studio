"""Persistence bridge from Training Worker result envelopes to legacy jobs."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_algorithm,
    crud_dataset,
    crud_model_version,
    crud_yolo_train_job,
)
from labelu.internal.domain.models.model_version import (
    ModelArtifact,
    ModelQualityEvidence,
    ModelVersion,
)
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus
from labelu.internal.common.training_worker import (
    TRAINING_PROTOCOL_VERSION,
    TRAINING_STAGES,
)


def _loads(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _worker_result_transition_allowed(current: str, target: str) -> bool:
    """Keep late/replayed worker results from regressing a job state."""
    current = str(current or YoloTrainStatus.PENDING.value).upper()
    target = str(target or "").upper()
    if current == target:
        return True
    if current in {
        YoloTrainStatus.COMPLETED.value,
        YoloTrainStatus.FAILED.value,
        YoloTrainStatus.CANCELLED.value,
    }:
        return False
    if current == YoloTrainStatus.PENDING.value:
        return target in {
            YoloTrainStatus.PROCESSING.value,
            YoloTrainStatus.COMPLETED.value,
            YoloTrainStatus.FAILED.value,
        }
    if current == YoloTrainStatus.PROCESSING.value:
        return target in {
            YoloTrainStatus.PENDING.value,
            YoloTrainStatus.PROCESSING.value,
            YoloTrainStatus.COMPLETED.value,
            YoloTrainStatus.FAILED.value,
        }
    return False


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _normalize_progress(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        progress = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker result progress must be a number") from exc
    if not math.isfinite(progress) or not 0 <= progress <= 100:
        raise ValueError("worker result progress must be between 0 and 100")
    return int(progress) if progress.is_integer() else progress


def _materialize_model_lineage(
    db: Session,
    *,
    job: YoloTrainJob,
    result: Mapping[str, Any],
    state: dict[str, Any],
) -> int | None:
    """Create canonical model records when a worker supplied explicit lineage.

    Legacy jobs do not carry enough information to safely infer an algorithm or
    immutable dataset version.  New adapters opt in with ``model_lineage``;
    malformed or incomplete lineage is recorded in job state and never turns a
    successful training task into an unverifiable model version.
    """

    if str(result.get("status") or "").upper() not in {"SUCCEEDED", "COMPLETED"}:
        return None
    lineage = result.get("model_lineage")
    if not isinstance(lineage, Mapping):
        return None
    required = ("algorithm_id", "dataset_version_id")
    if any(lineage.get(field) in (None, "") for field in required):
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "MODEL_LINEAGE_INCOMPLETE",
            "required": list(required),
        }
        return None
    try:
        algorithm_id = int(lineage["algorithm_id"])
        dataset_version_id = int(lineage["dataset_version_id"])
    except (TypeError, ValueError):
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "MODEL_LINEAGE_INVALID_IDS",
        }
        return None
    algorithm = crud_algorithm.get_definition_by_id(db, algorithm_id)
    dataset_version = crud_dataset.get_version(db, dataset_version_id)
    if algorithm is None or str(algorithm.status).upper() != "ACTIVE":
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "ALGORITHM_UNAVAILABLE",
        }
        return None
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == int(dataset_version.dataset_id))
        .one_or_none()
        if dataset_version is not None
        else None
    )
    if (
        dataset_version is None
        or dataset is None
        or str(dataset_version.status).upper() != "FROZEN"
    ):
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "DATASET_VERSION_NOT_FROZEN",
        }
        return None
    created_by = job.created_by or lineage.get("created_by")
    if created_by in (None, ""):
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "CREATOR_MISSING",
        }
        return None
    parent_id = lineage.get("parent_model_version_id") or getattr(
        job, "parent_model_version_id", None
    )
    if parent_id not in (None, ""):
        try:
            parent = crud_model_version.get(db, int(parent_id))
        except (TypeError, ValueError):
            parent = None
        if parent is None or int(parent.algorithm_id) != algorithm_id:
            state["model_materialization"] = {
                "status": "DEFERRED",
                "reason": "PARENT_SCOPE_MISMATCH",
            }
            return None

    existing = (
        db.query(ModelVersion)
        .filter(ModelVersion.training_job_id == int(job.id))
        .order_by(ModelVersion.id.asc())
        .first()
    )
    if existing is not None:
        state["model_materialization"] = {
            "status": "MATERIALIZED",
            "model_version_id": int(existing.id),
        }
        return int(existing.id)

    if crud_algorithm.lock_definition(db, algorithm_id) is None:
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "ALGORITHM_UNAVAILABLE",
        }
        return None
    version_no = crud_model_version.next_version_no(db, algorithm_id)
    branch_version_no = crud_model_version.next_branch_version_no(
        db,
        series_id=getattr(job, "series_id", None),
        variant_id=getattr(job, "variant_id", None),
    )
    model = ModelVersion(
        algorithm_id=algorithm_id,
        dataset_id=int(dataset.id),
        dataset_version_id=dataset_version_id,
        training_job_id=int(job.id),
        parent_model_version_id=int(parent_id) if parent_id not in (None, "") else None,
        series_id=getattr(job, "series_id", None),
        variant_id=getattr(job, "variant_id", None),
        inheritance_mode=getattr(job, "inheritance_mode", None),
        selection_contract_sha256=(
            getattr(job, "selection_contract_sha256", None)
            or lineage.get("selection_contract_sha256")
        ),
        model_key=f"{algorithm.algorithm_key}:v{version_no}",
        branch_version_no=branch_version_no,
        version_no=version_no,
        display_name=str(lineage.get("display_name") or f"训练任务 #{job.id}")[:256],
        status="CANDIDATE",
        chip_type=lineage.get("chip_type"),
        precision=lineage.get("precision"),
        input_contract_json=(
            json.dumps(
                lineage.get("input_contract"), ensure_ascii=False, separators=(",", ":")
            )
            if isinstance(lineage.get("input_contract"), Mapping)
            else None
        ),
        output_contract_json=(
            json.dumps(
                lineage.get("output_contract"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(lineage.get("output_contract"), Mapping)
            else None
        ),
        preprocess_contract_json=(
            json.dumps(
                lineage.get("preprocess_contract"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(lineage.get("preprocess_contract"), Mapping)
            else None
        ),
        postprocess_contract_json=(
            json.dumps(
                lineage.get("postprocess_contract"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(lineage.get("postprocess_contract"), Mapping)
            else None
        ),
        metrics_json=(
            json.dumps(result.get("metrics"), ensure_ascii=False, sort_keys=True)
            if isinstance(result.get("metrics"), Mapping)
            else None
        ),
        manifest_json=(
            json.dumps(lineage.get("manifest"), ensure_ascii=False, sort_keys=True)
            if isinstance(lineage.get("manifest"), Mapping)
            else None
        ),
        created_by=int(created_by),
    )
    crud_model_version.create(db, model)

    artifact_errors: list[str] = []
    artifacts = result.get("artifacts")
    if isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes)):
        for item in artifacts:
            if not isinstance(item, Mapping):
                continue
            artifact_type = str(
                item.get("artifact_type") or item.get("type") or ""
            ).lower()
            relative_path = str(
                item.get("relative_path") or item.get("path") or ""
            ).replace("\\", "/")
            sha256 = str(item.get("sha256") or "").lower()
            path = PurePosixPath(relative_path)
            if (
                artifact_type not in {"pt", "onnx", "bmodel", "manifest"}
                or not relative_path
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or not _valid_sha256(sha256)
            ):
                artifact_errors.append(relative_path or artifact_type or "unknown")
                continue
            crud_model_version.create_artifact(
                db,
                ModelArtifact(
                    model_version_id=int(model.id),
                    artifact_type=artifact_type,
                    relative_path=relative_path,
                    sha256=sha256,
                    size_bytes=item.get("size_bytes"),
                    chip_type=item.get("chip_type"),
                    precision=item.get("precision"),
                    metadata_json=(
                        json.dumps(
                            item.get("metadata"), ensure_ascii=False, sort_keys=True
                        )
                        if isinstance(item.get("metadata"), Mapping)
                        else None
                    ),
                    created_by=int(created_by),
                ),
            )

    evidence = result.get("quality_evidence")
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
        for item in evidence:
            if not isinstance(item, Mapping) or str(
                item.get("gate_type") or ""
            ).lower() not in {
                "training",
                "generalization",
                "conversion",
                "edge",
                "release",
            }:
                continue
            gate_type = str(item["gate_type"]).lower()
            status = str(item.get("status") or "PENDING").upper()
            if status not in {"PENDING", "PASS", "FAIL"}:
                continue
            evidence_sha256 = item.get("evidence_sha256")
            if evidence_sha256 and not _valid_sha256(evidence_sha256):
                continue
            crud_model_version.create_evidence(
                db,
                ModelQualityEvidence(
                    model_version_id=int(model.id),
                    gate_type=gate_type,
                    revision=int(item.get("revision") or 1),
                    status=status,
                    metrics_json=(
                        json.dumps(
                            item.get("metrics"), ensure_ascii=False, sort_keys=True
                        )
                        if isinstance(item.get("metrics"), Mapping)
                        else None
                    ),
                    evidence_uri=item.get("evidence_uri"),
                    evidence_sha256=(
                        str(evidence_sha256).lower() if evidence_sha256 else None
                    ),
                    evaluator=item.get("evaluator"),
                    reason=item.get("reason"),
                    created_by=int(created_by),
                ),
            )
    state["model_materialization"] = {
        "status": "MATERIALIZED",
        "model_version_id": int(model.id),
        "artifact_errors": artifact_errors,
    }
    return int(model.id)


def persist_training_worker_result(
    db: Session,
    *,
    job_id: int,
    result: Mapping[str, Any],
    fencing_check=None,
) -> bool:
    """Apply one idempotent result to a non-YOLO container training job.

    Legacy YOLO jobs continue to be updated by their existing process worker;
    this bridge therefore ignores ``yolo_detection`` results.  A caller may
    provide ``fencing_check`` to reject stale scheduler/worker ownership before
    any database mutation.
    """

    job = db.query(YoloTrainJob).filter_by(id=int(job_id)).one_or_none()
    if job is None:
        raise ValueError(f"training job not found: {job_id}")
    if str(getattr(job, "job_type", "") or "").strip() == "yolo_detection":
        return False
    protocol_version = str(result.get("protocol_version") or "").strip()
    if protocol_version != TRAINING_PROTOCOL_VERSION:
        raise ValueError(
            f"unsupported training worker result protocol: {protocol_version}"
        )
    stage = str(result.get("stage") or "").strip().lower()
    if stage not in TRAINING_STAGES:
        raise ValueError(f"unsupported training worker result stage: {stage}")
    worker_id = str(result.get("worker_id") or "").strip()
    if not worker_id:
        raise ValueError("worker result worker_id is required")
    result_adapter = str(result.get("adapter_key") or "").strip().lower()
    job_adapter = str(getattr(job, "job_type", "") or "").strip().lower()
    if result_adapter and result_adapter != job_adapter:
        return False
    command_id = str(result.get("command_id") or "").strip()
    if not command_id:
        raise ValueError("worker result command_id is required")
    if fencing_check is not None:
        token = result.get("fencing_token")
        if token is not None and not fencing_check(int(token)):
            return False

    status = str(result.get("status") or "").upper()
    if status in {"ACCEPTED", "RUNNING", "PROCESSING"}:
        target = YoloTrainStatus.PROCESSING.value
    elif status in {"SUCCEEDED", "COMPLETED"}:
        target = YoloTrainStatus.COMPLETED.value
    elif status in {"FAILED", "DEAD_LETTER"}:
        target = YoloTrainStatus.FAILED.value
    elif status == "FENCING_REJECTED":
        target = YoloTrainStatus.PENDING.value
    else:
        raise ValueError(f"unsupported worker result status: {status}")

    state = _loads(getattr(job, "remote_status_json", None))
    processed = state.setdefault("worker_result_command_ids", [])
    if not isinstance(processed, list):
        processed = []
        state["worker_result_command_ids"] = processed
    if command_id in processed:
        return False

    if not _worker_result_transition_allowed(str(job.status or ""), target):
        # A replacement worker may have already reached a terminal state.  The
        # late result is a harmless replay and must not consume the Outbox
        # retry budget or mutate the terminal job.
        return False

    fencing_token = result.get("fencing_token")
    if fencing_token is not None:
        try:
            token = int(fencing_token)
        except (TypeError, ValueError) as exc:
            raise ValueError("worker result fencing_token must be an integer") from exc
        previous_token = state.get("worker_fencing_token")
        try:
            previous_token = int(previous_token) if previous_token is not None else None
        except (TypeError, ValueError):
            previous_token = None
        if previous_token is not None and token < previous_token:
            return False
        state["worker_fencing_token"] = token

    processed.append(command_id)
    state["worker_result"] = dict(result)
    state["worker_result_at"] = datetime.now().isoformat()
    state["worker_result_status"] = status
    progress = _normalize_progress(result.get("progress"))
    if progress is not None:
        state["worker_progress"] = progress
    # New model adapters opt into canonical lineage materialization.  Keep the
    # legacy job update successful even when optional lineage is incomplete.
    try:
        # A savepoint prevents a partially-created model from surviving when a
        # malformed artifact or database constraint is encountered.
        with db.begin_nested():
            _materialize_model_lineage(db, job=job, result=result, state=state)
    except Exception as exc:  # pragma: no cover - defensive boundary
        state["model_materialization"] = {
            "status": "DEFERRED",
            "reason": "MATERIALIZATION_ERROR",
            "message": str(exc)[:1000],
        }
    job.remote_status_json = json.dumps(state, ensure_ascii=False, sort_keys=True)

    metrics = result.get("metrics")
    metrics_json = (
        json.dumps(metrics, ensure_ascii=False, sort_keys=True)
        if isinstance(metrics, Mapping)
        else None
    )
    error = result.get("message") if target == YoloTrainStatus.FAILED.value else None
    crud_yolo_train_job.update_status(
        db,
        job,
        target,
        metrics_summary=metrics_json or getattr(job, "metrics_summary", None),
        error_message=str(error)[:4000] if error else None,
    )
    return True


__all__ = ["persist_training_worker_result"]
