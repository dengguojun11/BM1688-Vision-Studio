from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_outbox import OutboxDeferredError
from labelu.internal.common.auto_training_runtime import load_auto_training_switches
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


_DISPATCH_LEASE_SECONDS = 300


def _lease_available(owner_column, expires_column, now: datetime):
    """Return a fail-closed queue/execution lease availability predicate.

    A row with an owner but no expiry is malformed and must not be treated as
    reclaimable.  Only an explicitly empty owner or an explicitly expired
    lease may be claimed by another dispatcher.
    """
    return or_(
        owner_column.is_(None),
        and_(expires_column.isnot(None), expires_column <= now),
    )


def _claim_training_dispatch(
    db: Session, *, job_id: int, now: datetime
) -> tuple[bool, str]:
    """Conditionally claim an automatic training handoff.

    The claim is deliberately short-lived: the worker owns the longer
    execution lifecycle, while this lease prevents two dispatchers from
    submitting the same PENDING job at the same time.
    """
    owner = f"training-dispatch:{os.getpid()}:{uuid.uuid4().hex}"
    claimed = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.id == int(job_id),
            YoloTrainJob.status == "PENDING",
            YoloTrainJob.source_type == "auto_training",
            YoloTrainJob.auto_training_plan_id.isnot(None),
            _lease_available(
                YoloTrainJob.queue_lease_owner,
                YoloTrainJob.queue_lease_expires_at,
                now,
            ),
        )
        .update(
            {
                YoloTrainJob.queue_lease_owner: owner,
                YoloTrainJob.queue_lease_expires_at: now + timedelta(seconds=300),
            },
            synchronize_session="fetch",
        )
    )
    if claimed == 1:
        db.flush()
        return True, owner
    return False, owner


def _release_training_dispatch_claim(
    db: Session, *, job_id: int, owner: str
) -> None:
    db.query(YoloTrainJob).filter(
        YoloTrainJob.id == int(job_id),
        YoloTrainJob.queue_lease_owner == owner,
    ).update(
        {
            YoloTrainJob.queue_lease_owner: None,
            YoloTrainJob.queue_lease_expires_at: datetime.now(),
        },
        synchronize_session="fetch",
    )


def _claim_bm1688_dispatch(
    db: Session, *, export_id: int, now: datetime
) -> tuple[bool, str]:
    owner = f"bm1688-dispatch:{os.getpid()}:{uuid.uuid4().hex}"
    claimed = (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.status == "PENDING",
            YoloBm1688Export.auto_training_round_id.isnot(None),
            YoloBm1688Export.pipeline_origin.in_(
                ("AUTO_QUALITY", "MANUAL_DIAGNOSTIC")
            ),
            _lease_available(
                YoloBm1688Export.execution_lease_owner,
                YoloBm1688Export.execution_lease_expires_at,
                now,
            ),
            _lease_available(
                YoloBm1688Export.queue_lease_owner,
                YoloBm1688Export.queue_lease_expires_at,
                now,
            ),
        )
        .update(
            {
                YoloBm1688Export.queue_lease_owner: owner,
                YoloBm1688Export.queue_lease_expires_at: now
                + timedelta(seconds=_DISPATCH_LEASE_SECONDS),
            },
            synchronize_session="fetch",
        )
    )
    if claimed == 1:
        db.flush()
        return True, owner
    return False, owner


def _release_bm1688_dispatch_claim(
    db: Session, *, export_id: int, owner: str
) -> None:
    db.query(YoloBm1688Export).filter(
        YoloBm1688Export.id == int(export_id),
        YoloBm1688Export.queue_lease_owner == owner,
    ).update(
        {
            YoloBm1688Export.queue_lease_owner: None,
            YoloBm1688Export.queue_lease_expires_at: datetime.now(),
        },
        synchronize_session="fetch",
    )


def _claim_edge_validation_dispatch(
    db: Session, *, edge_job_id: int, now: datetime
) -> tuple[bool, str]:
    owner = f"edge-dispatch:{os.getpid()}:{uuid.uuid4().hex}"
    claimed = (
        db.query(YoloEdgeValidationJob)
        .filter(
            YoloEdgeValidationJob.id == int(edge_job_id),
            YoloEdgeValidationJob.status.in_(("PENDING", "QUEUED")),
            YoloEdgeValidationJob.auto_training_round_id.isnot(None),
            YoloEdgeValidationJob.pipeline_origin.in_(
                ("AUTO_QUALITY", "MANUAL_DIAGNOSTIC")
            ),
            _lease_available(
                YoloEdgeValidationJob.execution_lease_owner,
                YoloEdgeValidationJob.execution_lease_expires_at,
                now,
            ),
            _lease_available(
                YoloEdgeValidationJob.queue_lease_owner,
                YoloEdgeValidationJob.queue_lease_expires_at,
                now,
            ),
        )
        .update(
            {
                YoloEdgeValidationJob.queue_lease_owner: owner,
                YoloEdgeValidationJob.queue_lease_expires_at: now
                + timedelta(seconds=_DISPATCH_LEASE_SECONDS),
            },
            synchronize_session="fetch",
        )
    )
    if claimed == 1:
        db.flush()
        return True, owner
    return False, owner


def _release_edge_validation_dispatch_claim(
    db: Session, *, edge_job_id: int, owner: str
) -> None:
    db.query(YoloEdgeValidationJob).filter(
        YoloEdgeValidationJob.id == int(edge_job_id),
        YoloEdgeValidationJob.queue_lease_owner == owner,
    ).update(
        {
            YoloEdgeValidationJob.queue_lease_owner: None,
            YoloEdgeValidationJob.queue_lease_expires_at: datetime.now(),
        },
        synchronize_session="fetch",
    )


def claim_bm1688_recovery(
    db: Session,
    *,
    export_id: int,
    status: str,
    now: datetime,
) -> tuple[bool, str]:
    """Claim one automatic export before restarting its worker.

    Recovery is intentionally separate from normal PENDING dispatch: a stale
    PROCESSING row may be resumed, but only one scheduler instance may own the
    restart handoff. Manual exports are not eligible for this helper.
    """
    owner = f"bm1688-recovery:{os.getpid()}:{uuid.uuid4().hex}"
    claimed = (
        db.query(YoloBm1688Export)
        .filter(
            YoloBm1688Export.id == int(export_id),
            YoloBm1688Export.status == str(status),
            YoloBm1688Export.auto_training_round_id.isnot(None),
            YoloBm1688Export.pipeline_origin.in_(
                ("AUTO_QUALITY", "MANUAL_DIAGNOSTIC")
            ),
            _lease_available(
                YoloBm1688Export.execution_lease_owner,
                YoloBm1688Export.execution_lease_expires_at,
                now,
            ),
            _lease_available(
                YoloBm1688Export.queue_lease_owner,
                YoloBm1688Export.queue_lease_expires_at,
                now,
            ),
        )
        .update(
            {
                YoloBm1688Export.queue_lease_owner: owner,
                YoloBm1688Export.queue_lease_expires_at: now
                + timedelta(seconds=_DISPATCH_LEASE_SECONDS),
            },
            synchronize_session="fetch",
        )
    )
    if claimed == 1:
        db.flush()
        return True, owner
    return False, owner


def release_bm1688_recovery(
    db: Session, *, export_id: int, owner: str
) -> None:
    db.query(YoloBm1688Export).filter(
        YoloBm1688Export.id == int(export_id),
        YoloBm1688Export.queue_lease_owner == owner,
    ).update(
        {
            YoloBm1688Export.queue_lease_owner: None,
            YoloBm1688Export.queue_lease_expires_at: datetime.now(),
        },
        synchronize_session="fetch",
    )


def claim_edge_validation_recovery(
    db: Session,
    *,
    edge_job_id: int,
    status: str,
    now: datetime,
) -> tuple[bool, str]:
    """Claim one automatic edge job before restarting its worker."""
    owner = f"edge-recovery:{os.getpid()}:{uuid.uuid4().hex}"
    claimed = (
        db.query(YoloEdgeValidationJob)
        .filter(
            YoloEdgeValidationJob.id == int(edge_job_id),
            YoloEdgeValidationJob.status == str(status),
            YoloEdgeValidationJob.auto_training_round_id.isnot(None),
            YoloEdgeValidationJob.pipeline_origin.in_(
                ("AUTO_QUALITY", "MANUAL_DIAGNOSTIC")
            ),
            _lease_available(
                YoloEdgeValidationJob.execution_lease_owner,
                YoloEdgeValidationJob.execution_lease_expires_at,
                now,
            ),
            _lease_available(
                YoloEdgeValidationJob.queue_lease_owner,
                YoloEdgeValidationJob.queue_lease_expires_at,
                now,
            ),
        )
        .update(
            {
                YoloEdgeValidationJob.queue_lease_owner: owner,
                YoloEdgeValidationJob.queue_lease_expires_at: now
                + timedelta(seconds=_DISPATCH_LEASE_SECONDS),
            },
            synchronize_session="fetch",
        )
    )
    if claimed == 1:
        db.flush()
        return True, owner
    return False, owner


def release_edge_validation_recovery(
    db: Session, *, edge_job_id: int, owner: str
) -> None:
    db.query(YoloEdgeValidationJob).filter(
        YoloEdgeValidationJob.id == int(edge_job_id),
        YoloEdgeValidationJob.queue_lease_owner == owner,
    ).update(
        {
            YoloEdgeValidationJob.queue_lease_owner: None,
            YoloEdgeValidationJob.queue_lease_expires_at: datetime.now(),
        },
        synchronize_session="fetch",
    )


def enqueue_training_worker_command(
    db: Session,
    *,
    job: YoloTrainJob,
    command: Any | None = None,
) -> Any:
    """Publish one idempotent ``training.worker.command.v1`` event.

    The command event is deliberately a bridge around the existing
    ``YoloTrainJob``.  It carries lineage/routing metadata outside the worker
    command while preserving the legacy job status machine.  A deterministic
    command id makes retries and scheduler replays harmless.
    """
    from labelu.internal.application.service.auto_training_events import (
        enqueue_auto_training_event,
    )
    from labelu.internal.application.service.yolo_train_queue import command_from_job
    from labelu.internal.adapter.persistence import crud_algorithm
    from labelu.internal.common.algorithm_registry import get_training_adapter
    from labelu.internal.common.training_worker import make_command
    from labelu.internal.domain.models.auto_training import AutoTrainingPlan
    from labelu.internal.domain.models.task import Task

    plan = None
    if getattr(job, "auto_training_plan_id", None) is not None:
        plan = (
            db.query(AutoTrainingPlan)
            .filter_by(id=int(job.auto_training_plan_id))
            .one_or_none()
        )
        if plan is None:
            raise ValueError("training job auto training plan was not found")
    task = (
        db.query(Task).filter_by(id=int(job.task_id)).one_or_none()
        if getattr(job, "task_id", None) is not None
        else None
    )
    job_project_id = getattr(job, "project_id", None)
    task_project_id = getattr(task, "project_id", None) if task is not None else None
    plan_project_id = getattr(plan, "project_id", None) if plan is not None else None
    project_ids = {
        int(value)
        for value in (job_project_id, task_project_id, plan_project_id)
        if value is not None
    }
    if len(project_ids) > 1:
        raise ValueError("training job project ownership does not match its plan/task")
    project_id = next(iter(project_ids), None)

    adapter_key = str(getattr(job, "job_type", None) or "").strip().lower()
    if not adapter_key:
        raise ValueError("training job adapter key is required")
    adapter = get_training_adapter(adapter_key)
    if command is None:
        command = command_from_job(job)
    raw_job_hyperparams = _loads_json(getattr(job, "hyperparams_json", None))
    if hasattr(command, "dict"):
        request = command.dict(exclude_none=True)
    elif isinstance(command, dict):
        request = dict(command)
    else:
        raise TypeError("training command must be a mapping or Pydantic model")
    request.update(
        {
            "job_id": int(job.id),
            "task_id": int(job.task_id) if job.task_id is not None else None,
            "project_id": project_id,
            "plan_id": (
                int(job.auto_training_plan_id)
                if job.auto_training_plan_id is not None
                else None
            ),
            "round_id": (
                int(job.auto_training_round_id)
                if job.auto_training_round_id is not None
                else None
            ),
            "dataset_manifest": _loads_json(
                getattr(job, "dataset_manifest_json", None)
            ),
        }
    )
    request = {key: value for key, value in request.items() if value is not None}
    # Carry canonical model lineage when the plan has an immutable dataset
    # binding.  The worker result can then materialize ModelVersion/Artifact
    # without guessing from legacy job names; missing or non-frozen versions
    # are intentionally left for the result bridge to defer.
    # Product capabilities may share the YOLO adapter while retaining their
    # own algorithm definition (for example smoke_detection or
    # street_litter_detection). Prefer the normalized algorithm key persisted
    # in the job hyperparameters; fall back to the adapter key for legacy jobs.
    lineage_algorithm_key = raw_job_hyperparams.get("algorithm_key") or adapter_key
    algorithm_definition = crud_algorithm.get_definition(db, str(lineage_algorithm_key))
    if algorithm_definition is not None:
        registered_adapter = str(getattr(algorithm_definition, "training_adapter", "") or "").strip().lower()
        if registered_adapter and registered_adapter != adapter_key:
            algorithm_definition = None
    # Manual unified-training jobs carry the frozen dataset binding in their
    # normalized hyperparameters.  Auto-training jobs carry it on the plan.
    # Both paths must emit the same lineage so a worker result can materialize
    # ModelVersion without guessing from a legacy job name.
    lineage_dataset_version_id = (
        getattr(plan, "source_dataset_version_id", None) if plan is not None else None
    )
    if lineage_dataset_version_id is None:
        candidate_dataset_version_id = request.get("dataset_version_id")
        if candidate_dataset_version_id is None:
            hyperparams = request.get("hyperparams")
            if isinstance(hyperparams, dict):
                candidate_dataset_version_id = hyperparams.get("dataset_version_id")
        if candidate_dataset_version_id is None:
            candidate_dataset_version_id = raw_job_hyperparams.get("dataset_version_id")
        try:
            if candidate_dataset_version_id is not None:
                lineage_dataset_version_id = int(candidate_dataset_version_id)
        except (TypeError, ValueError):
            lineage_dataset_version_id = None
    if algorithm_definition is not None and lineage_dataset_version_id:
        parent_model_version_id = None
        weights_ref = str(getattr(job, "model_weights", None) or "").strip()
        if weights_ref.startswith("trained:"):
            from labelu.internal.adapter.persistence import crud_model_version

            try:
                parent_job_id = int(weights_ref.split(":", 1)[1])
            except (TypeError, ValueError):
                parent_job_id = 0
            parent = (
                crud_model_version.get_by_training_job(db, parent_job_id)
                if parent_job_id > 0
                else None
            )
            if parent is not None and int(parent.algorithm_id) == int(algorithm_definition.id):
                parent_model_version_id = int(parent.id)
        request["model_lineage"] = {
            "project_id": project_id,
            "algorithm_id": int(algorithm_definition.id),
            "dataset_version_id": int(lineage_dataset_version_id),
            "display_name": f"{adapter_key} training job #{int(job.id)}",
            "chip_type": request.get("chip_type"),
            "precision": request.get("precision"),
            "manifest": request.get("dataset_manifest"),
            **(
                {"parent_model_version_id": parent_model_version_id}
                if parent_model_version_id is not None
                else {}
            ),
        }
    container_image = request.get("container_image")
    if not container_image:
        hyperparams = request.get("hyperparams")
        if isinstance(hyperparams, dict):
            candidate = hyperparams.get("container_image") or hyperparams.get(
                "worker_image"
            )
            if isinstance(candidate, str) and candidate.strip():
                container_image = candidate.strip()
    if not container_image and plan is not None and plan.current_version_id is not None:
        from labelu.internal.domain.models.auto_training import AutoTrainingPlanVersion

        version = (
            db.query(AutoTrainingPlanVersion)
            .filter_by(id=int(plan.current_version_id))
            .one_or_none()
        )
        config = _loads_json(getattr(version, "config_json", None)) if version else {}
        policy = config.get("training_policy")
        if isinstance(policy, dict):
            image = policy.get("container_image") or policy.get("worker_image")
            if isinstance(image, str) and image.strip():
                container_image = image.strip()
                request["container_image"] = container_image
    execution_mode = str(getattr(adapter, "execution_mode", "in_process"))
    command_id = "auto-training-job-{}-{}".format(
        int(job.id), str(getattr(job, "execution_signature", None) or job.id)[:24]
    )
    if execution_mode == "container" and not container_image:
        raise ValueError(f"{adapter_key} worker command requires container_image")
    worker_command = make_command(
        adapter_key=adapter_key,
        stage="train",
        request=request,
        execution_mode=execution_mode,
        command_id=command_id,
        container_image=container_image if execution_mode == "container" else None,
    )
    return enqueue_auto_training_event(
        db,
        event_name="training.worker.command.v1",
        aggregate_type="yolo_train_job",
        aggregate_id=str(job.id),
        payload={
            "job_id": int(job.id),
            "project_id": project_id,
            "plan_id": request.get("plan_id"),
            "round_id": request.get("round_id"),
            "command": worker_command,
        },
        idempotency_key=f"training-worker-command:{job.id}:{command_id}",
    )


def _loads_json(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = [
    "enqueue_training_worker_command",
    "dispatch_training_job",
    "dispatch_bm1688_export",
    "dispatch_edge_validation_job",
    "claim_bm1688_recovery",
    "release_bm1688_recovery",
    "claim_edge_validation_recovery",
    "release_edge_validation_recovery",
]


def dispatch_training_job(
    db: Session,
    *,
    job_id: int,
    switches: dict[str, bool] | None = None,
    submit: Callable[[int, int, Any], Any] | None = None,
    produce: Callable[[Session, YoloTrainJob, Any], Any] | None = None,
) -> bool:
    active_switches = switches or load_auto_training_switches()
    if active_switches.get("observe_only", False) or not active_switches.get(
        "train_enabled", False
    ):
        raise OutboxDeferredError(
            "auto training dispatch is disabled", delay_seconds=60
        )
    job = db.query(YoloTrainJob).filter_by(id=job_id).one()
    if job.source_type != "auto_training" or job.auto_training_plan_id is None:
        raise ValueError("training job is not managed by an auto training plan")
    if job.status != "PENDING":
        return False
    # The command bridge is opt-in while the legacy in-process YOLO queue
    # remains the compatibility path.  Only registered container adapters are
    # routed here; YOLO/plate jobs continue through their existing workers.
    worker_command_enabled = bool(
        active_switches.get("worker_command_enabled", False)
        or os.environ.get("LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    if worker_command_enabled and str(job.job_type or "").lower() not in {
        "yolo_detection",
        "plate_detection",
        "plate_recognition",
    }:
        from labelu.internal.application.service.yolo_train_queue import (
            command_from_job,
        )

        # The deterministic key is known before command construction. Reuse a
        # prior non-terminal event so retries remain successful and harmless.
        from labelu.internal.domain.models.auto_training import AutoTrainingOutbox

        command_id = "auto-training-job-{}-{}".format(
            int(job.id), str(getattr(job, "execution_signature", None) or job.id)[:24]
        )
        idempotency_key = f"training-worker-command:{job.id}:{command_id}"
        existing_event = (
            db.query(AutoTrainingOutbox)
            .filter_by(idempotency_key=idempotency_key)
            .first()
        )
        if existing_event is not None and existing_event.status != "DEAD_LETTER":
            return True
        claimed, claim_owner = _claim_training_dispatch(
            db, job_id=int(job.id), now=datetime.now()
        )
        if not claimed:
            return False
        try:
            worker_command = command_from_job(job)
            if produce is None:
                enqueue_training_worker_command(db, job=job, command=worker_command)
            else:
                produce(db, job, worker_command)
            return True
        except Exception:
            _release_training_dispatch_claim(
                db, job_id=int(job.id), owner=claim_owner
            )
            raise
    if submit is None:
        from labelu.internal.application.service.yolo_train import (
            _submit_yolo_train_job,
        )
        from labelu.internal.application.service.yolo_train_queue import (
            command_from_job,
        )

        submit = _submit_yolo_train_job
        command = command_from_job(job)
    else:
        from labelu.internal.application.service.yolo_train_queue import (
            command_from_job,
        )

        command = command_from_job(job)
    claimed, claim_owner = _claim_training_dispatch(
        db, job_id=int(job.id), now=datetime.now()
    )
    if not claimed:
        return False
    try:
        submit(int(job.id), int(job.task_id), command)
        return True
    except Exception:
        _release_training_dispatch_claim(
            db, job_id=int(job.id), owner=claim_owner
        )
        raise


def dispatch_bm1688_export(
    db: Session,
    *,
    export_id: int,
    switches: dict[str, bool] | None = None,
    submit: Callable[[int], Any] | None = None,
) -> bool:
    # A dedicated BM1688 process is the sole consumer of automatic PENDING
    # exports. The API/outbox path must defer without claiming or starting a
    # Web-process thread, otherwise two consumers can race the same export.
    from labelu.internal.application.service.bm1688_worker_runtime import (
        dedicated_worker_enabled,
    )

    if dedicated_worker_enabled():
        raise OutboxDeferredError(
            "BM1688 独立 Worker 已启用，由独立 Worker 领取编译任务",
            delay_seconds=30,
        )
    active_switches = switches or load_auto_training_switches()
    if active_switches.get("observe_only", False) or not active_switches.get(
        "bm1688_enabled", False
    ):
        raise OutboxDeferredError(
            "automatic BM1688 dispatch is disabled", delay_seconds=60
        )
    export = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    if export.auto_training_round_id is None or export.pipeline_origin not in {
        "AUTO_QUALITY",
        "MANUAL_DIAGNOSTIC",
    }:
        raise ValueError("BM1688 export is not managed by auto training")
    if export.status != "PENDING":
        return False
    claimed, claim_owner = _claim_bm1688_dispatch(
        db, export_id=int(export.id), now=datetime.now()
    )
    if not claimed:
        return False
    try:
        if submit is None:
            import threading

            from labelu.internal.application.service.yolo_bm1688_export import (
                _run_bm1688_export_sync,
            )

            submit = lambda eid: threading.Thread(
                target=_run_bm1688_export_sync,
                args=(eid,),
                name=f"auto-bm1688-export-{eid}",
                daemon=True,
            ).start()
        submit(int(export.id))
        return True
    except Exception:
        _release_bm1688_dispatch_claim(
            db, export_id=int(export.id), owner=claim_owner
        )
        raise


def _edge_pack_request(job) -> tuple[dict, list[int], dict[int, int] | None]:
    payload = {}
    try:
        payload = json.loads(job.result_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    request = payload.get("request") if isinstance(payload, dict) else {}
    request = request if isinstance(request, dict) else {}
    try:
        raw_ids = request.get("sample_ids") or json.loads(job.sample_ids_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw_ids = []
    sample_ids = [int(value) for value in raw_ids]
    raw_task_ids = request.get("sample_task_ids") or {}
    sample_task_ids = (
        {
            int(sample_id): int(task_id)
            for sample_id, task_id in raw_task_ids.items()
        }
        if isinstance(raw_task_ids, dict) and raw_task_ids
        else None
    )
    return request, sample_ids, sample_task_ids


def _edge_pack_signature(
    *,
    export_id: int,
    job_id: int,
    sample_ids: list[int],
    sample_task_ids: dict[int, int] | None,
    attachment_ids: list[int],
    task_config_json: str | None,
    source_evidence: list[dict] | None = None,
) -> str:
    payload = {
        "export_id": int(export_id),
        "job_id": int(job_id),
        "sample_ids": sorted(int(value) for value in sample_ids),
        "sample_task_ids": {
            str(key): int(value)
            for key, value in sorted((sample_task_ids or {}).items())
        },
        "attachment_ids": sorted(int(value) for value in attachment_ids),
        "task_config": task_config_json or "",
        "source_evidence": source_evidence or [],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _edge_pack_source_evidence(db: Session, sample_ids: list[int]) -> list[dict]:
    from labelu.internal.domain.models.attachment import TaskAttachment
    from labelu.internal.domain.models.sample import TaskSample

    rows = (
        db.query(TaskSample)
        .filter(TaskSample.id.in_(sample_ids))
        .all()
        if sample_ids
        else []
    )
    attachment_ids = {
        int(getattr(row, "file_id", 0) or 0)
        for row in rows
        if getattr(row, "file_id", None)
    }
    attachments = (
        db.query(TaskAttachment)
        .filter(TaskAttachment.id.in_(attachment_ids))
        .all()
        if attachment_ids
        else []
    )
    attachment_map = {int(row.id): row for row in attachments}
    evidence = []
    for row in sorted(rows, key=lambda item: int(item.id)):
        attachment = attachment_map.get(int(row.file_id or 0))
        evidence.append(
            {
                "sample_id": int(row.id),
                "task_id": int(row.task_id),
                "annotation_sha256": hashlib.sha256(
                    str(getattr(row, "data", None) or "").encode("utf-8")
                ).hexdigest(),
                "sample_updated_at": str(getattr(row, "updated_at", None) or ""),
                "file_id": int(row.file_id or 0),
                "attachment_path": str(getattr(attachment, "path", "") or ""),
                "attachment_sha256": str(getattr(attachment, "sha256", "") or ""),
                "attachment_size_bytes": int(
                    getattr(attachment, "size_bytes", 0) or 0
                ),
                "attachment_updated_at": str(
                    getattr(attachment, "updated_at", None) or ""
                ),
            }
        )
    return evidence


def _replace_edge_pack_staging(*, staging: Path, final: Path) -> None:
    """Publish an immutable signature directory safely on local filesystems.

    Windows can briefly deny a directory rename while an indexer, antivirus
    scanner, or another worker still has a handle open.  Retry that transient
    condition and accept a complete package published concurrently for the
    same signature.  A partial target is never treated as a successful
    publication.
    """
    import time
    import shutil

    def _reuse_complete_target() -> bool:
        if not (final / "manifest.json").is_file():
            return False
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return True

    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        if _reuse_complete_target():
            return
        raise FileExistsError(f"edge validation pack already exists incomplete: {final}")

    last_error: OSError | None = None
    for attempt in range(8):
        try:
            staging.replace(final)
            return
        except FileExistsError:
            if _reuse_complete_target():
                return
            raise
        except PermissionError as exc:
            last_error = exc
            if _reuse_complete_target():
                return
            if attempt == 7:
                break
            time.sleep(0.15 * (attempt + 1))
    raise PermissionError(
        f"无法发布边端验证样本包，临时目录可能仍被占用: {staging} -> {final}"
    ) from last_error


def _release_edge_validation_pack_claim(
    *, edge_job_id: int, owner: str, db_factory
) -> None:
    from labelu.internal.common.db import begin_transaction

    with db_factory() as db:
        with begin_transaction(db):
            db.query(YoloEdgeValidationJob).filter(
                YoloEdgeValidationJob.id == int(edge_job_id),
                YoloEdgeValidationJob.status == "PENDING",
                YoloEdgeValidationJob.queue_lease_owner == owner,
            ).update(
                {
                    YoloEdgeValidationJob.queue_lease_owner: None,
                    YoloEdgeValidationJob.queue_lease_expires_at: datetime.now(),
                },
                synchronize_session=False,
            )


def _prepare_edge_validation_pack(*, edge_job_id: int, owner: str) -> bool:
    """Build and commit an automatic edge pack before device scheduling."""
    import shutil

    from labelu.internal.application.service.yolo_edge_validation_pack import (
        edge_validation_job_root,
        edge_validation_pack_ready,
        pack_selected_samples_for_job,
    )
    from labelu.internal.common.db import begin_transaction, session_scope
    from labelu.internal.domain.models.task import Task

    staging: Path | None = None
    final: Path | None = None
    try:
        with session_scope() as db:
            job = (
                db.query(YoloEdgeValidationJob)
                .filter(
                    YoloEdgeValidationJob.id == int(edge_job_id),
                    YoloEdgeValidationJob.status == "PENDING",
                    YoloEdgeValidationJob.queue_lease_owner == owner,
                )
                .one_or_none()
            )
            if job is None:
                return False
            request, sample_ids, sample_task_ids = _edge_pack_request(job)
            task = db.query(Task).filter_by(id=int(job.task_id)).one()
            signature = _edge_pack_signature(
                export_id=int(job.export_id),
                job_id=int(job.id),
                sample_ids=sample_ids,
                sample_task_ids=sample_task_ids,
                attachment_ids=[],
                task_config_json=getattr(task, "config", None),
                source_evidence=_edge_pack_source_evidence(db, sample_ids),
            )
            try:
                existing_payload = json.loads(job.result_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                existing_payload = {}
            existing_pack = (
                existing_payload.get("pack")
                if isinstance(existing_payload, dict)
                else None
            )
            if (
                edge_validation_pack_ready(job)
                and isinstance(existing_pack, dict)
                and existing_pack.get("pack_signature") == signature
            ):
                with begin_transaction(db):
                    updated = (
                        db.query(YoloEdgeValidationJob)
                        .filter(
                            YoloEdgeValidationJob.id == int(edge_job_id),
                            YoloEdgeValidationJob.status == "PENDING",
                            YoloEdgeValidationJob.queue_lease_owner == owner,
                            YoloEdgeValidationJob.queue_lease_expires_at
                            > datetime.now(),
                        )
                        .update(
                            {
                                YoloEdgeValidationJob.status: "PENDING",
                                YoloEdgeValidationJob.queue_lease_owner: None,
                                YoloEdgeValidationJob.queue_lease_expires_at: None,
                                YoloEdgeValidationJob.queue_reason: "waiting_for_dispatch",
                                YoloEdgeValidationJob.queued_at: datetime.now(),
                            },
                            synchronize_session=False,
                        )
                    )
                    return int(updated or 0) == 1
            final = edge_validation_job_root(
                export_id=int(job.export_id), job_id=int(job.id)
            ) / "packs" / signature
            manifest_path = final / "manifest.json"
            if final.is_dir() and manifest_path.is_file():
                manifest = {}
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    manifest = {}
                pack = {
                    "job_root": str(final),
                    "eval_data_root": str(final / "eval_data"),
                    "manifest_path": str(manifest_path),
                    "copied_count": int(manifest.get("copied_count") or 0),
                    "labeled_count": int(manifest.get("labeled_count") or 0),
                    "unlabeled_count": int(manifest.get("unlabeled_count") or 0),
                    "skipped_count": len(manifest.get("skipped") or []),
                    "file_mappings": manifest.get("file_mappings") or [],
                    "skipped": manifest.get("skipped") or [],
                }
            else:
                staging = final.parent / f".{signature}.pack-{uuid.uuid4().hex}"
                pack = pack_selected_samples_for_job(
                    db,
                    export_id=int(job.export_id),
                    job_id=int(job.id),
                    task_id=int(job.task_id),
                    mode=str(job.mode),
                    sample_ids=sample_ids,
                    sample_task_ids=sample_task_ids,
                    attachment_ids=[],
                    task_config_json=getattr(task, "config", None),
                    root_override=staging,
                )
                _replace_edge_pack_staging(staging=staging, final=final)
                staging = None
            pack.update(
                {
                    "state": "READY",
                    "pack_signature": signature,
                    "job_root": str(final),
                    "eval_data_root": str(final / "eval_data"),
                    "manifest_path": str(final / "manifest.json"),
                    "prepared_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            with begin_transaction(db):
                current = (
                    db.query(YoloEdgeValidationJob)
                    .filter(
                        YoloEdgeValidationJob.id == int(edge_job_id),
                        YoloEdgeValidationJob.status == "PENDING",
                        YoloEdgeValidationJob.queue_lease_owner == owner,
                        YoloEdgeValidationJob.queue_lease_expires_at > datetime.now(),
                    )
                    .one_or_none()
                )
                if current is None:
                    raise RuntimeError("edge validation pack lease lost")
                payload = {}
                try:
                    payload = json.loads(current.result_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                payload["request"] = {
                    **request,
                    "sample_ids": sample_ids,
                    "attachment_ids": [],
                }
                payload["pack"] = pack
                updated = (
                    db.query(YoloEdgeValidationJob)
                    .filter(
                        YoloEdgeValidationJob.id == int(edge_job_id),
                        YoloEdgeValidationJob.status == "PENDING",
                        YoloEdgeValidationJob.queue_lease_owner == owner,
                        YoloEdgeValidationJob.queue_lease_expires_at > datetime.now(),
                    )
                    .update(
                        {
                            YoloEdgeValidationJob.status: "PENDING",
                            YoloEdgeValidationJob.result_json: json.dumps(
                                payload, ensure_ascii=False
                            ),
                            YoloEdgeValidationJob.image_count: int(
                                pack.get("copied_count") or 0
                            ),
                            YoloEdgeValidationJob.labeled_image_count: int(
                                pack.get("labeled_count") or 0
                            ),
                            YoloEdgeValidationJob.unlabeled_image_count: int(
                                pack.get("unlabeled_count") or 0
                            ),
                            YoloEdgeValidationJob.queue_reason: "waiting_for_dispatch",
                            YoloEdgeValidationJob.queued_at: datetime.now(),
                            YoloEdgeValidationJob.queue_lease_owner: None,
                            YoloEdgeValidationJob.queue_lease_expires_at: None,
                            YoloEdgeValidationJob.report_summary: (
                                "自动边端验证样本包已准备，共 "
                                f"{int(pack.get('copied_count') or 0)} 张图片"
                            ),
                        },
                        synchronize_session=False,
                    )
                )
                if int(updated or 0) != 1:
                    raise RuntimeError("edge validation pack lease lost")
            return True
    except Exception:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def dispatch_edge_validation_job(
    db: Session,
    *,
    edge_job_id: int,
    switches: dict[str, bool] | None = None,
    submit: Callable[[int], Any] | None = None,
) -> bool:
    active_switches = switches or load_auto_training_switches()
    if active_switches.get("observe_only", False) or not active_switches.get(
        "edge_validation_enabled", False
    ):
        raise OutboxDeferredError(
            "automatic edge validation dispatch is disabled", delay_seconds=60
        )
    from labelu.internal.common.db import begin_transaction, session_scope

    # The outbox handler wraps its callback in a transaction. Do not use that
    # session for the handoff: the queue claim must commit before any files are
    # built, and no worker thread is started from this callback.
    with session_scope() as claim_db:
        with begin_transaction(claim_db):
            job = (
                claim_db.query(YoloEdgeValidationJob)
                .filter_by(id=int(edge_job_id))
                .one()
            )
            if job.auto_training_round_id is None or job.pipeline_origin not in {
                "AUTO_QUALITY",
                "MANUAL_DIAGNOSTIC",
            }:
                raise ValueError("edge validation job is not managed by auto training")
            if job.status not in {"PENDING", "QUEUED"}:
                return False
            claimed, claim_owner = _claim_edge_validation_dispatch(
                claim_db, edge_job_id=int(job.id), now=datetime.now()
            )
            if not claimed:
                return False

    try:
        if not _prepare_edge_validation_pack(
            edge_job_id=int(edge_job_id), owner=claim_owner
        ):
            return False
        # Test/integration callers may inject a post-commit notifier. The
        # production path leaves the READY row for the scheduler poller; it
        # never enqueues or starts SSH work inside the outbox transaction.
        if submit is not None:
            submit(int(edge_job_id))
        # The scheduler/watchdog polls committed READY rows. Do not start its
        # thread here: this function is also called inside an outbox
        # transaction, whose commit boundary belongs to the caller.
        return True
    except Exception:
        _release_edge_validation_pack_claim(
            edge_job_id=int(edge_job_id),
            owner=claim_owner,
            db_factory=session_scope,
        )
        raise
