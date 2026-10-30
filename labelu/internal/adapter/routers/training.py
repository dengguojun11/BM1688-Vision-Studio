"""Unified training contract for the 7GAI platform.

The legacy ``/yolo-*`` routes remain available for old clients.  New clients
use this router so algorithm registration, automatic task snapshots, dataset
lineage and worker dispatch are enforced in one place.
"""

import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import exists, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_algorithm,
    crud_dataset,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_train_job,
)
from labelu.internal.application.command.training import CreateTrainingJobCommand
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.task import TaskTrainingCandidateResponse
from labelu.internal.application.response.training import (
    TrainingHistoryResponse,
    TrainingIterationPreviewResponse,
    TrainingJobResponse,
)
from labelu.internal.application.service import training_iteration as training_iteration_service
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service import dataset as dataset_service
from labelu.internal.application.service import yolo_train_evaluation
from labelu.internal.application.service import auto_training_plan as auto_training_plan_service
from labelu.internal.application.service import task as task_service
from labelu.internal.application.service import model_series as model_series_service
from labelu.internal.application.service import annotation_contract
from labelu.internal.application.service.auto_training_dispatch import (
    enqueue_training_worker_command,
)
from labelu.internal.application.service import algorithm as algorithm_service
from labelu.internal.application.service.algorithm import ensure_seeded
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common import db as db_module
from labelu.internal.common.algorithm_registry import get_training_adapter
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.idempotency import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    normalize_idempotency_key,
    request_payload_hash,
)
from labelu.internal.common.security import security
from labelu.internal.common.task_access import (
    is_consistent_pool_admin,
    is_task_superuser,
    user_has_task_access,
)
from labelu.internal.common.training_contract import (
    branch_identity_sha256,
    build_selection_contract,
    canonical_runtime_profile,
    canonical_training_recipe_key,
    canonical_selection_contract,
    selection_contract_json,
    selection_contract_sha256,
)
from labelu.internal.common.plate_recognition_profile import (
    PLATE_DETECTION_PRESET_SLUG,
    PLATE_DETECTION_PROFILE,
    PLATE_RECOGNITION_PRESET_SLUG,
    PLATE_RECOGNITION_PROFILE,
)
from labelu.internal.common.yolo_training_metrics import (
    actual_epochs_for_train_job,
    parse_results_csv_history,
    resolve_train_dir_from_weights,
    train_job_work_train_dir,
)
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.annotation_taxonomy import (
    DatasetCategory,
    TrainingRecipe,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

router = APIRouter(prefix="/training", tags=["training"])


def _error(message: str, http_status: int = 400) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=http_status,
        message=message,
    )


def _json_list(raw: Any) -> list[Any]:
    """Decode a catalog JSON list without allowing malformed metadata to leak."""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(value) if isinstance(value, list) else []


def _unified_selection_contract(
    cmd: CreateTrainingJobCommand,
    *,
    dataset_id: int,
    dataset_version_id: int,
    task_ids: list[int],
    class_order: list[str],
    label_set_signature_value: str | None,
    recipe_key: str | None,
    recipe_version: int | None,
) -> tuple[dict[str, Any], str, str]:
    """Create the server-authoritative branch contract for ``/training/jobs``."""

    generated = build_selection_contract(
        dataset_id=dataset_id,
        dataset_ids=getattr(cmd, "dataset_ids", None) or ([dataset_id] if dataset_id else []),
        dataset_version_id=dataset_version_id,
        mode=str((cmd.params or {}).get("mode") or "task").strip() or "task",
        task_ids=task_ids,
        selected_label_ids=[int(value) for value in cmd.label_ids],
        sample_conditions=cmd.sample_conditions,
        annotation_contract={
            "class_order": list(class_order),
            "label_set_signature": label_set_signature_value,
        },
        recipe={
            "training_recipe_key": recipe_key,
            "training_recipe_version": recipe_version,
            "algorithm_key": cmd.algorithm_key,
        },
    )
    supplied = canonical_selection_contract(cmd.selection_contract)
    supplied.update(generated)
    supplied["annotation_contract"] = {
        **(
            supplied.get("annotation_contract")
            if isinstance(supplied.get("annotation_contract"), dict)
            else {}
        ),
        **generated["annotation_contract"],
    }
    supplied["recipe"] = {
        **(
            supplied.get("recipe")
            if isinstance(supplied.get("recipe"), dict)
            else {}
        ),
        **generated["recipe"],
    }
    contract = canonical_selection_contract(supplied)
    digest = selection_contract_sha256(contract)
    branch_digest = branch_identity_sha256(contract)
    if cmd.branch_key and str(cmd.branch_key).strip() not in {
        branch_digest,
        digest,
    }:
        raise _error("branch_key 与当前训练选择合同不一致，请刷新预览后重试", 409)
    return contract, digest, selection_contract_json(contract)


def _response(
    job: Any,
    *,
    algorithm_key: str,
    adapter_key: str,
    dataset_version_id: int | None,
    params: dict[str, Any],
    task_name: str | None = None,
) -> TrainingJobResponse:
    status_value = getattr(job, "status", "")
    status_value = getattr(status_value, "value", status_value)
    manifest = params.get("dataset_manifest")
    if not isinstance(manifest, dict):
        try:
            manifest = json.loads(getattr(job, "dataset_manifest_json", None) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            manifest = None
        if not isinstance(manifest, dict):
            manifest = None
    selection_contract = params.get("selection_contract")
    if not isinstance(selection_contract, dict):
        try:
            selection_contract = json.loads(
                getattr(job, "selection_contract_json", None) or "{}"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            selection_contract = None
        if not isinstance(selection_contract, dict):
            selection_contract = None
    sample_conditions = params.get("sample_conditions")
    if not isinstance(sample_conditions, dict):
        try:
            sample_conditions = json.loads(
                getattr(job, "sample_conditions_json", None) or "{}"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            sample_conditions = {}
        if not isinstance(sample_conditions, dict):
            sample_conditions = {}
    metrics = None
    try:
        parsed_metrics = json.loads(getattr(job, "metrics_summary", None) or "{}")
        if isinstance(parsed_metrics, dict):
            metrics = parsed_metrics
    except (TypeError, ValueError, json.JSONDecodeError):
        metrics = None
    current_epoch, progress_percent = _training_progress(job, params)
    series_name = params.get("series_name")
    variant_name = params.get("variant_name")
    model_family = params.get("model_family")
    return TrainingJobResponse(
        id=int(job.id),
        task_id=int(job.task_id),
        algorithm_key=algorithm_key,
        adapter_key=adapter_key,
        dataset_version_id=int(dataset_version_id) if dataset_version_id else None,
        series_id=getattr(job, "series_id", None),
        variant_id=getattr(job, "variant_id", None),
        series_name=str(series_name).strip() if series_name else None,
        variant_name=str(variant_name).strip() if variant_name else None,
        model_family=str(model_family).strip() if model_family else None,
        branch_key=getattr(job, "branch_key", None),
        selection_contract_sha256=getattr(job, "selection_contract_sha256", None),
        selection_contract=selection_contract,
        sample_conditions=sample_conditions,
        parent_model_version_id=getattr(job, "parent_model_version_id", None),
        parent_training_job_id=getattr(job, "parent_training_job_id", None),
        inheritance_mode=getattr(job, "inheritance_mode", None),
        status=str(status_value),
        execution_mode=str(getattr(job, "execution_mode", "local") or "local"),
        container_image=params.get("container_image"),
        epochs=getattr(job, "epochs", None),
        current_epoch=current_epoch,
        progress_percent=progress_percent,
        imgsz=getattr(job, "imgsz", None),
        train_sample_count=getattr(job, "train_sample_count", None),
        val_sample_count=getattr(job, "val_sample_count", None),
        model_weights=getattr(job, "model_weights", None),
        params=params,
        task_name=task_name,
        name=f"训练作业 #{int(job.id)}",
        train_node_id=getattr(job, "train_node_id", None),
        runtime_status=(
            getattr(job, "status", None).value
            if hasattr(getattr(job, "status", None), "value")
            else getattr(job, "status", None)
        ),
        failure_reason=getattr(job, "error_message", None),
        metrics=metrics,
        dataset_manifest=manifest,
        created_at=getattr(job, "created_at", None),
        updated_at=getattr(job, "updated_at", None),
    )


def _job_params(job: Any) -> dict[str, Any]:
    try:
        value = json.loads(getattr(job, "hyperparams_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _job_dataset_manifest(job: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the immutable dataset reference carried by a training job."""
    values = params if isinstance(params, dict) else _job_params(job)
    manifest = values.get("dataset_manifest")
    if isinstance(manifest, dict):
        return manifest
    try:
        manifest = json.loads(getattr(job, "dataset_manifest_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        manifest = {}
    return manifest if isinstance(manifest, dict) else {}


def _job_dataset_version_id(job: Any, params: dict[str, Any] | None = None) -> int | None:
    values = params if isinstance(params, dict) else _job_params(job)
    manifest = _job_dataset_manifest(job, values)
    for value in (values.get("dataset_version_id"), manifest.get("dataset_version_id")):
        try:
            if value is not None:
                parsed = int(value)
                if parsed > 0:
                    return parsed
        except (TypeError, ValueError):
            continue
    return None


def _positive_lineage_int(value: Any) -> int | None:
    """Parse a positive integer used by an immutable dataset reference."""
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dataset_lineage_reference(manifest: Any) -> tuple[int, int, int, str, str] | None:
    """Return the canonical DatasetVersion reference from a manifest object.

    A replay must carry all of the immutable address components.  Accepting
    only ``dataset_version_id`` is unsafe because historical/corrupt rows can
    point at a valid version while the copied dataset or manifest metadata is
    from a different resource.
    """
    if not isinstance(manifest, dict):
        return None
    dataset_version_id = _positive_lineage_int(manifest.get("dataset_version_id"))
    dataset_id = _positive_lineage_int(manifest.get("dataset_id"))
    version_no = _positive_lineage_int(manifest.get("version_no"))
    manifest_path = manifest.get("manifest_rel_path")
    manifest_sha = manifest.get("manifest_sha256")
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        return None
    if not isinstance(manifest_sha, str):
        return None
    manifest_sha = manifest_sha.strip().lower()
    if len(manifest_sha) != 64 or any(
        char not in "0123456789abcdef" for char in manifest_sha
    ):
        return None
    if dataset_version_id is None or dataset_id is None or version_no is None:
        return None
    return (
        dataset_version_id,
        dataset_id,
        version_no,
        manifest_path.strip(),
        manifest_sha,
    )


def _manifest_has_complete_reference_fields(manifest: Any) -> bool:
    """Tell malformed full-looking manifests apart from old partial rows."""
    if not isinstance(manifest, dict):
        return False
    return all(
        key in manifest
        for key in (
            "dataset_version_id",
            "dataset_id",
            "version_no",
            "manifest_rel_path",
            "manifest_sha256",
        )
    )


def _parse_stored_dataset_manifest(job: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(getattr(job, "dataset_manifest_json", None) or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _job_has_complete_dataset_lineage(
    db: Session,
    job: Any,
    params: dict[str, Any] | None = None,
    *,
    expected_task_id: int | None = None,
    expected_dataset_id: int | None = None,
    expected_dataset_version_id: int | None = None,
    expected_job_type: str | None = None,
) -> bool:
    """Require a real frozen DatasetVersion before short-circuiting a replay.

    Older unified jobs can have the same idempotency key but no immutable
    manifest because the lineage write used to happen after queue submission.
    Those rows must continue through the snapshot path so the caller can repair
    them instead of returning a job that workers cannot reproduce.
    """
    values = params if isinstance(params, dict) else _job_params(job)
    # Do not fall back to the duplicate column here.  A historical row with
    # only ``dataset_manifest_json`` is incomplete and must go through the
    # repair path rather than being returned as a reproducible replay.
    manifest = values.get("dataset_manifest")
    reference = _dataset_lineage_reference(manifest)
    if reference is None:
        return False
    version_id, dataset_id, version_no, manifest_path, manifest_sha = reference
    if expected_task_id is not None:
        try:
            if int(getattr(job, "task_id", 0) or 0) != int(expected_task_id):
                return False
        except (TypeError, ValueError):
            return False
    if expected_dataset_id is not None:
        expected_id = _positive_lineage_int(expected_dataset_id)
        if expected_id is None or dataset_id != expected_id:
            return False
    if expected_dataset_version_id is not None:
        expected_version_id = _positive_lineage_int(expected_dataset_version_id)
        if expected_version_id is None or version_id != expected_version_id:
            return False
    if expected_job_type is not None:
        expected_type = str(expected_job_type or "").strip().lower()
        actual_type = str(
            getattr(job, "job_type", None) or getattr(job, "category", None) or ""
        ).strip().lower()
        if expected_type and actual_type != expected_type:
            return False
    try:
        top_level_version_id = values.get("dataset_version_id")
        if (
            top_level_version_id is not None
            and _positive_lineage_int(top_level_version_id) != version_id
        ):
            return False
        top_level_dataset_id = values.get("dataset_id")
        if (
            top_level_dataset_id is not None
            and _positive_lineage_int(top_level_dataset_id) != dataset_id
        ):
            return False
        top_level_version_no = values.get("version_no")
        if (
            top_level_version_no is not None
            and _positive_lineage_int(top_level_version_no) != version_no
        ):
            return False
    except (TypeError, ValueError):
        return False
    # New unified jobs persist the same immutable reference in both columns.
    # Requiring the duplicate to parse and agree prevents a partially repaired
    # historical row from being returned as a successful replay.
    try:
        stored_manifest = json.loads(
            getattr(job, "dataset_manifest_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(stored_manifest, dict):
        return False
    if _dataset_lineage_reference(stored_manifest) != reference:
        return False
    version = crud_dataset.get_version(db, version_id)
    if version is None or str(getattr(version, "status", "")).upper() != "FROZEN":
        return False
    actual_reference = (
        _positive_lineage_int(getattr(version, "id", None)),
        _positive_lineage_int(getattr(version, "dataset_id", None)),
        _positive_lineage_int(getattr(version, "version_no", None)),
        str(getattr(version, "manifest_rel_path", "") or "").strip(),
        str(getattr(version, "manifest_sha256", "") or "").strip().lower(),
    )
    if actual_reference != reference:
        return False
    # A task may be legacy/unbound (explicit version requests are still
    # supported), but once it has a logical dataset binding the replay must not
    # return a job frozen from a different dataset.
    task_id = _positive_lineage_int(getattr(job, "task_id", None))
    if task_id is None:
        return False
    task = db.query(Task).filter(Task.id == task_id).one_or_none()
    task_dataset_id = getattr(task, "dataset_id", None) if task is not None else None
    if task is None:
        return False
    if task_dataset_id is not None:
        task_dataset_id = _positive_lineage_int(task_dataset_id)
        if task_dataset_id is None or task_dataset_id != dataset_id:
            return False
    return True


def _job_has_conflicting_dataset_lineage(
    db: Session,
    job: Any,
    params: dict[str, Any] | None = None,
    *,
    expected_task_id: int | None = None,
    expected_dataset_id: int | None = None,
    expected_dataset_version_id: int | None = None,
    expected_job_type: str | None = None,
) -> bool:
    """Detect a persisted lineage contradiction before the repair path.

    Rows created before immutable lineage was introduced may have no manifest,
    or only a partial copy, and remain repairable.  Once a row contains a
    complete reference, however, silently replacing it with a newly generated
    snapshot would violate idempotency and could train a different dataset.
    """
    values = params if isinstance(params, dict) else _job_params(job)
    embedded_manifest = values.get("dataset_manifest")
    stored_manifest = _parse_stored_dataset_manifest(job)
    embedded_reference = _dataset_lineage_reference(embedded_manifest)
    stored_reference = _dataset_lineage_reference(stored_manifest)

    # A task or adapter mismatch is unsafe even when the old row has no
    # manifest: the idempotency key must never retarget another job.
    if expected_task_id is not None:
        if _positive_lineage_int(getattr(job, "task_id", None)) != _positive_lineage_int(
            expected_task_id
        ):
            return True
    if expected_job_type is not None:
        expected_type = str(expected_job_type or "").strip().lower()
        actual_type = str(
            getattr(job, "job_type", None) or getattr(job, "category", None) or ""
        ).strip().lower()
        if expected_type and actual_type and actual_type != expected_type:
            return True

    # No complete reference means this is the historical repair case.  A
    # full-looking object whose values are malformed is treated as a
    # contradiction rather than accepted as a reproducible lineage.
    if embedded_reference is None and stored_reference is None:
        return _manifest_has_complete_reference_fields(
            embedded_manifest
        ) or _manifest_has_complete_reference_fields(stored_manifest)
    if (
        embedded_reference is not None
        and stored_reference is not None
        and embedded_reference != stored_reference
    ):
        return True

    reference = embedded_reference or stored_reference
    assert reference is not None
    version_id, dataset_id, version_no, manifest_path, manifest_sha = reference
    if expected_dataset_id is not None:
        if dataset_id != _positive_lineage_int(expected_dataset_id):
            return True
    if expected_dataset_version_id is not None:
        if version_id != _positive_lineage_int(expected_dataset_version_id):
            return True

    for key, expected in (
        ("dataset_version_id", version_id),
        ("dataset_id", dataset_id),
        ("version_no", version_no),
    ):
        value = values.get(key)
        if value is not None and _positive_lineage_int(value) != expected:
            return True

    version = crud_dataset.get_version(db, version_id)
    if version is None or str(getattr(version, "status", "")).upper() != "FROZEN":
        return True
    actual_reference = (
        _positive_lineage_int(getattr(version, "id", None)),
        _positive_lineage_int(getattr(version, "dataset_id", None)),
        _positive_lineage_int(getattr(version, "version_no", None)),
        str(getattr(version, "manifest_rel_path", "") or "").strip(),
        str(getattr(version, "manifest_sha256", "") or "").strip().lower(),
    )
    if actual_reference != reference:
        return True

    task_id = _positive_lineage_int(getattr(job, "task_id", None))
    task = (
        db.query(Task).filter(Task.id == task_id).one_or_none()
        if task_id is not None
        else None
    )
    if task is None:
        return True
    task_dataset_id = getattr(task, "dataset_id", None)
    if task_dataset_id is not None and _positive_lineage_int(task_dataset_id) != dataset_id:
        return True
    return False


def _requeue_pending_training_job(
    db: Session, job: Any, *, adapter_key: str
) -> None:
    """Recover a committed job whose first worker handoff did not finish.

    A valid immutable manifest proves the data binding, but it does not prove
    that queue/outbox publication happened.  Requeue only a still-PENDING row;
    the YOLO queue and generic outbox use job-scoped idempotency guards for
    same-process retries.
    """
    status_value = getattr(job, "status", "")
    status_value = getattr(status_value, "value", status_value)
    if str(status_value or "").strip().upper() not in {"", "PENDING"}:
        return

    normalized_adapter = str(adapter_key or "").strip().lower()
    job_type = str(getattr(job, "job_type", "") or "").strip().lower()
    if normalized_adapter == "yolo_detection" or job_type == "yolo_detection":
        # Reconstruct from the persisted row so a replay payload cannot alter
        # the command that was originally accepted for this idempotency key.
        from labelu.internal.application.service.yolo_train_queue import (
            command_from_job,
        )

        dispatch_cmd = command_from_job(job)
        yolo_train_service._submit_yolo_train_job(
            int(job.id), int(job.task_id), dispatch_cmd
        )
        return

    # The request session may have an autobegun read transaction from the
    # replay checks.  Publish the generic worker event in a fresh transaction.
    with begin_transaction(db):
        refreshed = crud_yolo_train_job.get(db, int(job.id))
        if refreshed is None:
            return
        refreshed_status = getattr(refreshed, "status", "")
        refreshed_status = getattr(refreshed_status, "value", refreshed_status)
        if str(refreshed_status or "").strip().upper() in {"", "PENDING"}:
            enqueue_training_worker_command(db, job=refreshed)


def _training_progress(job: Any, params: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return the real epoch progress for the unified training list.

    The worker writes the authoritative epoch boundary to ``results.csv`` and
    the existing YOLO service already knows how to read it. Reuse that reader
    instead of estimating progress from status or elapsed time.
    """
    target_raw = getattr(job, "epochs", None) or params.get("epochs")
    try:
        target_epochs = max(1, int(float(target_raw)))
    except (TypeError, ValueError):
        target_epochs = None

    current: int | None = None
    candidates = [params.get("current_epoch"), params.get("actual_epochs")]
    last_metrics = params.get("last_epoch_metrics")
    if isinstance(last_metrics, dict):
        candidates.append(last_metrics.get("epoch"))
    for value in candidates:
        try:
            if value is not None:
                current = max(0, int(float(value)))
                break
        except (TypeError, ValueError):
            continue

    if current is None:
        try:
            current = actual_epochs_for_train_job(
                job_id=int(job.id),
                weights_path=getattr(job, "weights_path", None),
                metrics_summary=getattr(job, "metrics_summary", None),
            )
        except (OSError, TypeError, ValueError):
            current = None

    raw_status = getattr(job, "status", "")
    status = getattr(raw_status, "value", raw_status)
    status = str(status or "").strip().upper()
    if current is None and status in {"COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE"} and target_epochs is not None:
        current = target_epochs
    if current is None and status == "PENDING":
        current = 0
    if current is not None and target_epochs is not None:
        current = min(current, target_epochs)
        return current, min(100, max(0, round(current / target_epochs * 100)))
    return current, None


def _accessible_job(
    db: Session, job_id: int, current_user: User
) -> tuple[Any, Task]:
    """Load one training job through the source task access contract."""
    job = crud_yolo_train_job.get(db, job_id)
    if job is None:
        raise _error("训练作业不存在", 404)
    task = (
        db.query(Task).filter(Task.id == int(job.task_id)).one_or_none()
        if getattr(job, "task_id", None) is not None
        else None
    )
    if task is None:
        raise _error("训练作业关联任务不存在", 409)
    if not user_has_task_access(current_user, task):
        raise _error("训练作业不存在或无权访问", 404)
    return job, task


def _job_response(db: Session, job: Any, task: Task) -> TrainingJobResponse:
    params = _job_params(job)
    manifest = params.get("dataset_manifest")
    if not isinstance(manifest, dict):
        try:
            manifest = json.loads(getattr(job, "dataset_manifest_json", None) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            manifest = {}
        if isinstance(manifest, dict) and manifest.get("dataset_version_id"):
            params.setdefault("dataset_version_id", int(manifest["dataset_version_id"]))
    algorithm_key = str(
        params.get("algorithm_key")
        or getattr(job, "category", None)
        or getattr(job, "job_type", None)
        or ""
    )
    dataset_version_id = params.get("dataset_version_id")
    try:
        dataset_version_id = int(dataset_version_id) if dataset_version_id else None
    except (TypeError, ValueError):
        dataset_version_id = None
    response = _response(
        job,
        algorithm_key=algorithm_key,
        adapter_key=str(getattr(job, "job_type", "") or ""),
        dataset_version_id=dataset_version_id,
        params=params,
        task_name=getattr(task, "name", None),
    )
    from labelu.internal.application.service.training_iteration import (
        build_previous_version_comparison,
    )

    response.previous_version_comparison = build_previous_version_comparison(db, job)
    return response


def _training_recipe(
    db: Session, key: str | None, version: int | None
) -> TrainingRecipe | None:
    """Resolve an active recipe and keep its immutable metadata on the job.

    Recipes are global catalog entries.  They are optional for backwards
    compatibility, but an explicitly selected recipe must exist and be active
    so a worker never receives a dangling recipe reference.
    """
    normalized_key = canonical_training_recipe_key(key)
    if not normalized_key:
        return None
    query = db.query(TrainingRecipe).filter(
        TrainingRecipe.recipe_key == normalized_key,
        TrainingRecipe.status == "active",
        TrainingRecipe.deleted_at.is_(None),
    )
    if version is not None:
        query = query.filter(TrainingRecipe.version == int(version))
    return query.order_by(TrainingRecipe.version.desc()).first()


def _normalize_plate_training_contract(
    cmd: CreateTrainingJobCommand,
    recipe: TrainingRecipe | None,
) -> CreateTrainingJobCommand:
    """Bind unified requests to the dedicated legacy plate training path."""

    recipe_job_type = str(getattr(recipe, "job_type", "") or "").strip().lower()
    if recipe_job_type not in {"plate_detection", "plate_recognition"}:
        if cmd.rect_preset_slug or cmd.plate_training_target:
            raise _error("车牌训练字段只能用于车牌训练配方", 400)
        return cmd

    # Plate workers consume the saved preset/profile snapshot (rectangle plus
    # polygon geometry).  A label-dimension request has no such snapshot and
    # would only fail later in the worker, so reject the ambiguous contract at
    # the API boundary.
    if cmd.label_ids:
        raise _error("车牌训练必须按任务维度选择车牌标注，不能按标签维度训练", 400)

    expected_profile = (
        PLATE_DETECTION_PROFILE
        if recipe_job_type == "plate_detection"
        else PLATE_RECOGNITION_PROFILE
    )
    expected_preset = (
        PLATE_DETECTION_PRESET_SLUG
        if recipe_job_type == "plate_detection"
        else PLATE_RECOGNITION_PRESET_SLUG
    )
    requested_preset = str(cmd.rect_preset_slug or "").strip()
    if requested_preset and requested_preset != expected_preset:
        raise _error("车牌训练配方与标注预设不匹配", 400)
    requested_target = str(cmd.plate_training_target or "").strip()
    if requested_target and requested_target != recipe_job_type:
        raise _error("车牌训练目标与训练配方不匹配", 400)
    return cmd.copy(
        update={
            "rect_preset_slug": expected_preset,
            "plate_training_target": recipe_job_type,
            "model_weights": cmd.model_weights or "yolov8n-pose.pt",
            "params": {
                **(cmd.params or {}),
                "training_profile": expected_profile,
                "runtime_profile": expected_profile,
                "runtime_interface_version": 1,
                "rect_preset_slug": expected_preset,
                "plate_training_target": recipe_job_type,
            },
        }
    )


def _recipe_json(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _lineage_class_names(*sources: dict[str, Any]) -> list[str]:
    """Extract an ordered class-name list from request/manifest snapshots."""

    raw: Any = None
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("class_names", "classes", "labels_snapshot_json"):
            if key in source and source.get(key) not in (None, "", []):
                raw = source.get(key)
                break
        if raw is not None:
            break
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = [raw] if raw.strip() else []
    if isinstance(raw, dict):
        raw = raw.get("labels") or raw.get("classes") or []
    if not isinstance(raw, (list, tuple)):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            value = item.get("label_value") or item.get("name") or item.get("value")
        else:
            value = item
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)
    return names


def _persist_unified_yolo_lineage(
    db: Session,
    job: Any,
    *,
    params: dict[str, Any],
) -> Any:
    """Attach the platform contract to a job created by the legacy YOLO service.

    ``create_train_job`` predates the unified contract and intentionally filters
    its ``hyperparams`` through the Ultralytics allow-list.  The compatibility
    route must retain the complete platform request (including dataset binding)
    so workers, model lineage, and the detail endpoint can resolve the frozen
    source without consulting mutable task state.
    """
    params = dict(params or {})
    recipe_key = canonical_training_recipe_key(params.get("training_recipe_key"))
    if recipe_key:
        params["training_recipe_key"] = recipe_key
    runtime_profile = canonical_runtime_profile(params.get("runtime_profile"))
    if runtime_profile:
        params["runtime_profile"] = runtime_profile

    existing: dict[str, Any] = {}
    try:
        loaded = json.loads(getattr(job, "hyperparams_json", None) or "{}")
        if isinstance(loaded, dict):
            existing = loaded
    except (TypeError, ValueError, json.JSONDecodeError):
        existing = {}

    # Keep values generated by the legacy service while allowing the unified
    # request to be authoritative for contract and lineage fields.
    existing.update(params)
    # ``project_id`` is a retired compatibility column. New unified jobs do
    # not write it; authorization is always derived from the source task.
    job.project_id = None
    recipe_key = str(params.get("training_recipe_key") or "").strip()
    if recipe_key and hasattr(job, "training_recipe_key"):
        job.training_recipe_key = recipe_key
        job.training_recipe_version = params.get("training_recipe_version")

    manifest = params.get("dataset_manifest")
    if not isinstance(manifest, dict):
        try:
            loaded_manifest = json.loads(
                getattr(job, "dataset_manifest_json", None) or "{}"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            loaded_manifest = {}
        manifest = loaded_manifest if isinstance(loaded_manifest, dict) else {}
    selection_filter = params.get("selection_filter")
    if not isinstance(selection_filter, dict):
        selection_filter = {}

    selection_contract = params.get("selection_contract")
    contract_was_supplied = isinstance(selection_contract, dict)
    if not isinstance(selection_contract, dict):
        try:
            selection_contract = json.loads(
                getattr(job, "selection_contract_json", None) or "{}"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            selection_contract = {}
        contract_was_supplied = bool(selection_contract)
    if contract_was_supplied:
        selection_contract = canonical_selection_contract(selection_contract)
        selection_contract_text = selection_contract_json(selection_contract)
        if hasattr(job, "selection_contract_json"):
            job.selection_contract_json = selection_contract_text
        selection_digest = selection_contract_sha256(selection_contract)
        if hasattr(job, "selection_contract_sha256"):
            job.selection_contract_sha256 = selection_digest
    for field in (
        "series_id",
        "variant_id",
        "parent_model_version_id",
        "parent_training_job_id",
        "inheritance_mode",
        "branch_key",
    ):
        value = params.get(field)
        if value not in (None, "") and hasattr(job, field):
            try:
                setattr(job, field, int(value) if field.endswith("_id") else str(value))
            except (TypeError, ValueError):
                pass
    if (
        contract_was_supplied
        and hasattr(job, "branch_key")
        and not str(getattr(job, "branch_key", "") or "").strip()
    ):
        job.branch_key = branch_identity_sha256(selection_contract)
    sample_conditions = params.get("sample_conditions")
    if isinstance(sample_conditions, dict) and hasattr(job, "sample_conditions_json"):
        job.sample_conditions_json = json.dumps(
            sample_conditions, ensure_ascii=False, separators=(",", ":")
        )

    # These columns are used by iteration/publish selectors.  Keep the full
    # manifest in JSON for replay, but also materialize its stable identifiers
    # so callers do not need to parse a fallback payload for every row.
    for field in (
        "label_set_signature",
        "class_order_signature",
        "data_yaml_sha256",
        "dataset_content_signature",
    ):
        value = manifest.get(field)
        if value in (None, ""):
            value = params.get(field)
        if value in (None, "") and field == "dataset_content_signature":
            value = manifest.get("content_sha256")
        if value not in (None, "") and hasattr(job, field):
            setattr(job, field, str(value).strip())

    if hasattr(job, "dataset_category_id"):
        category_id = selection_filter.get("dataset_category_id")
        if category_id in (None, ""):
            category_id = manifest.get("dataset_category_id")
        if category_id not in (None, ""):
            try:
                job.dataset_category_id = int(category_id)
            except (TypeError, ValueError):
                pass

    class_names = _lineage_class_names(params, manifest)
    if class_names and hasattr(job, "class_names_json"):
        job.class_names_json = json.dumps(class_names, ensure_ascii=False)
    if runtime_profile and hasattr(job, "runtime_profile"):
        job.runtime_profile = runtime_profile
    runtime_version = params.get("runtime_interface_version")
    if runtime_version is not None and hasattr(job, "runtime_interface_version"):
        try:
            job.runtime_interface_version = int(runtime_version)
        except (TypeError, ValueError):
            pass
    job.hyperparams_json = json.dumps(
        existing,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    job.dataset_manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


@router.get("/catalog", response_model=OkResp[dict[str, Any]])
async def training_catalog(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the dynamic training choices visible to the current user."""
    _ = authorization
    ensure_seeded(db)
    algorithms, _ = await algorithm_service.list_definitions(db, page=0, size=500)
    datasets, datasets_total = crud_dataset.list_datasets(
        db,
        page=0,
        size=500,
        user_id=None if is_task_superuser(current_user) else current_user.id,
    )
    dataset_ids = [int(item.id) for item in datasets]
    versions = (
        db.query(DatasetVersion)
        .filter(
            DatasetVersion.dataset_id.in_(dataset_ids) if dataset_ids else False,
            DatasetVersion.status == "FROZEN",
        )
        .order_by(DatasetVersion.id.desc())
        .limit(500)
        .all()
    )
    pending_versions = (
        db.query(DatasetVersion)
        .filter(
            DatasetVersion.dataset_id.in_(dataset_ids) if dataset_ids else False,
            DatasetVersion.status.in_(("INGESTING", "VALIDATING", "READY_TO_REVIEW")),
        )
        .order_by(DatasetVersion.id.desc())
        .limit(500)
        .all()
    )
    recipes = (
        db.query(TrainingRecipe)
        .filter(TrainingRecipe.status == "active", TrainingRecipe.deleted_at.is_(None))
        .order_by(TrainingRecipe.recipe_key, TrainingRecipe.version.desc())
        .limit(500)
        .all()
    )
    return OkResp[dict[str, Any]](
        data={
            "algorithms": [
                item.dict() if hasattr(item, "dict") else item.model_dump()
                for item in algorithms
            ],
            "dataset_versions": [
                {
                    "id": int(item.id),
                    "dataset_id": int(item.dataset_id),
                    "version_no": int(item.version_no),
                    "status": str(item.status),
                    "sample_count": int(item.sample_count or 0),
                    "label_set_signature": item.label_set_signature,
                    "created_at": item.created_at,
                    "frozen_at": item.frozen_at,
                }
                for item in versions
            ],
            "datasets": [
                {
                    "id": int(item.id),
                    "name": item.name,
                    "dataset_category_id": item.dataset_category_id,
                    "dataset_category_name": (
                        item.dataset_category.name if item.dataset_category else None
                    ),
                    "dataset_label_contract_id": getattr(
                        item, "dataset_label_contract_id", None
                    ),
                    "dataset_label_contract_revision": (
                        int(item.label_contract.revision)
                        if getattr(item, "label_contract", None)
                        else None
                    ),
                    "dataset_label_ids": _json_list(
                        getattr(item.label_contract, "label_ids_json", None)
                        if getattr(item, "label_contract", None)
                        else None
                    ),
                    "dataset_labels": _json_list(
                        getattr(item.label_contract, "labels_snapshot_json", None)
                        if getattr(item, "label_contract", None)
                        else None
                    ),
                }
                for item in datasets
            ],
            # The catalog is a fast first-screen cache.  The training wizard
            # uses GET /datasets for the authoritative searchable/paginated
            # browser, so callers can distinguish a truncated quick list from
            # an empty catalog instead of silently assuming it is complete.
            "datasets_total": int(datasets_total),
            "datasets_page": 0,
            "datasets_size": len(datasets),
            "datasets_truncated": int(datasets_total) > len(datasets),
            # Non-frozen versions are deliberately not training choices, but
            # exposing them lets the UI explain exactly why the selector is
            # empty and route the operator to version governance.
            "pending_dataset_versions": [
                {
                    "id": int(item.id),
                    "dataset_id": int(item.dataset_id),
                    "version_no": int(item.version_no),
                    "status": str(item.status),
                    "sample_count": int(item.sample_count or 0),
                }
                for item in pending_versions
            ],
            "training_recipes": [
                {
                    "id": int(item.id),
                    "recipe_key": item.recipe_key,
                    "version": int(item.version),
                    "name": item.name,
                    "job_type": item.job_type,
                    "model_family": item.model_family,
                    "annotation_kind": item.annotation_kind,
                    "component_contract": _recipe_json(item.component_contract_json),
                    "default_hyperparams": _recipe_json(item.default_hyperparams_json),
                    "status": item.status,
                }
                for item in recipes
            ],
        }
    )


@router.get(
    "/eligible-tasks",
    response_model=OkRespWithMeta[List[TaskTrainingCandidateResponse]],
)
async def training_eligible_tasks(
    training_dimension: str = Query(
        "task",
        description="训练维度：task=按任务，label=按标签聚合来源任务",
    ),
    dataset_id: int | None = Query(
        None, gt=0, description="逻辑数据集 ID；与分类、标签共同限定候选任务"
    ),
    dataset_ids: list[int] | None = Query(
        None, description="可兼容合并的逻辑数据集 ID，可重复传参"
    ),
    dataset_category_id: int | None = Query(
        None,
        gt=0,
        description="任务数据集分类 ID；与任务标签共同构成训练候选筛选条件",
    ),
    label_ids: list[int] | None = Query(
        None,
        description="任务当前标注契约必须覆盖的标签 ID，可重复传参实现多选",
    ),
    training_recipe_key: str | None = Query(
        None,
        max_length=128,
        description="任务维度训练所选配方；车牌配方用于筛选对应标注契约",
    ),
    search: str | None = Query(
        None,
        max_length=128,
        include_in_schema=False,
        description="按任务名称、任务描述或逻辑数据集名称筛选候选任务",
    ),
    q: str | None = Query(
        None,
        max_length=128,
        description="按任务名称、任务描述或逻辑数据集名称筛选候选任务",
    ),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return task candidates for the canonical training creator.

    The legacy ``/yolo-train/eligible-tasks`` route remains available for old
    clients, while new 7GAI pages use this route so the create flow has one
    API namespace and one project boundary.
    """
    _ = authorization
    if dataset_category_id is not None:
        category = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.id == int(dataset_category_id),
                DatasetCategory.deleted_at.is_(None),
                DatasetCategory.status == "active",
            )
            .one_or_none()
        )
        if category is None:
            raise _error("数据集分类不存在或已停用，请刷新分类列表", 400)
    selected_dataset_ids: list[int] = []
    if dataset_id is not None:
        selected_dataset_ids.append(int(dataset_id))
    for value in dataset_ids or []:
        if int(value) <= 0:
            raise _error("dataset_ids 只能包含正整数", 400)
        if int(value) not in selected_dataset_ids:
            selected_dataset_ids.append(int(value))
    for selected_dataset_id in selected_dataset_ids:
        dataset_service._dataset_or_raise(db, selected_dataset_id, current_user)
    candidate_kwargs: dict[str, Any] = {
        "page": page,
        "size": size,
    }
    # `q` is the canonical search parameter. Keep the old `search` name for
    # clients released before the unified list contract; a non-empty `q`
    # always wins when both are supplied.
    normalized_search = str(q if q is not None else search or "").strip()
    if normalized_search:
        candidate_kwargs["search"] = normalized_search
    if len(selected_dataset_ids) > 1:
        candidate_kwargs["dataset_ids"] = selected_dataset_ids
    elif selected_dataset_ids:
        candidate_kwargs["dataset_id"] = selected_dataset_ids[0]
    if dataset_category_id is not None:
        candidate_kwargs["dataset_category_ids"] = [int(dataset_category_id)]
    if label_ids:
        candidate_kwargs["label_ids"] = [int(value) for value in label_ids]
    normalized_recipe_key = str(training_recipe_key or "").strip()
    if normalized_recipe_key:
        candidate_kwargs["training_recipe_key"] = normalized_recipe_key
    normalized_dimension = str(training_dimension or "task").strip().lower()
    if normalized_dimension not in {"task", "label"}:
        raise _error("训练维度必须是 task 或 label", 400)
    if normalized_dimension == "label":
        if not label_ids:
            return OkRespWithMeta[List[TaskTrainingCandidateResponse]](
                meta_data=MetaData(total=0, page=page, size=0),
                data=[],
            )
        label_candidate_kwargs: dict[str, Any] = {
            "label_ids": [int(value) for value in label_ids],
            "page": page,
            "size": size,
            "dataset_id": selected_dataset_ids[0] if len(selected_dataset_ids) == 1 else None,
            "dataset_category_ids": (
                [int(dataset_category_id)]
                if dataset_category_id is not None
                else None
            ),
        }
        if len(selected_dataset_ids) > 1:
            label_candidate_kwargs["dataset_ids"] = selected_dataset_ids
        if normalized_search:
            label_candidate_kwargs["search"] = normalized_search
        data, total = await task_service.list_training_candidates_by_labels(
            db,
            current_user,
            **label_candidate_kwargs,
        )
    else:
        data, total = await task_service.list_task_dimension_training_candidates(
            db, current_user, **candidate_kwargs
        )
    return OkRespWithMeta[List[TaskTrainingCandidateResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


def _training_job_summary(db: Session, *, current_user: User) -> dict[str, Any]:
    """Aggregate visible training jobs without a project dimension."""
    filters = [
        Task.deleted_at.is_(None),
        Task.media_type == "IMAGE",
    ]
    if not is_task_superuser(current_user):
        collaborator_exists = exists().where(
            TaskCollaborator.task_id == Task.id,
            TaskCollaborator.user_id == int(current_user.id),
        )
        filters.append(
            or_(
                Task.created_by == int(current_user.id),
                collaborator_exists,
                Task.source_type == "third_party",
            )
        )

    query = (
        db.query(YoloTrainJob.status, func.count(YoloTrainJob.id))
        .join(Task, Task.id == YoloTrainJob.task_id)
        .filter(*filters)
    )
    grouped = query.group_by(YoloTrainJob.status).all()
    by_status = {
        str(getattr(raw_status, "value", raw_status) or "UNKNOWN"): int(count)
        for raw_status, count in grouped
    }
    active_statuses = {
        YoloTrainStatus.PENDING.value,
        YoloTrainStatus.PROCESSING.value,
    }
    return {
        "total": sum(by_status.values()),
        "by_status": dict(sorted(by_status.items())),
        "active_count": sum(by_status.get(item, 0) for item in active_statuses),
        "completed_count": by_status.get(YoloTrainStatus.COMPLETED.value, 0),
        "failed_count": by_status.get(YoloTrainStatus.FAILED.value, 0),
    }


@router.get("/summary", response_model=OkResp[dict[str, Any]])
async def training_summary(
    q: Optional[str] = Query(
        None,
        max_length=128,
        description="按自动训练计划、任务或报告提供方模糊搜索",
    ),
    search: Optional[str] = Query(
        None,
        max_length=128,
        include_in_schema=False,
        description="旧版搜索参数；q 有值时优先使用 q",
    ),
    page: Optional[int] = Query(None, ge=0),
    size: Optional[int] = Query(None, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return one read-only summary for all training views."""
    _ = authorization
    # Keep the no-parameter response compatible with earlier clients while
    # using one bounded page size for the new server-backed history lists.
    summary_page = 0 if page is None else int(page)
    summary_size = 20 if size is None else int(size)
    normalized_query = str(q if q is not None else search or "").strip() or None
    legacy_rows, legacy_total = yolo_train_evaluation.list_eval_reports_global(
        db,
        current_user,
        report_kind="full_analysis",
        page=summary_page,
        size=summary_size,
        search=normalized_query,
    )
    legacy_reports = [
        item.dict() if hasattr(item, "dict") else item.model_dump()
        for item in legacy_rows
    ]
    # The platform training page is deliberately project-free.  Reuse the
    # canonical plan visibility/payload logic so the unified summary does not
    # drift from the retained automatic-training history API.
    auto_plans, auto_plan_total = auto_training_plan_service.list_plans(
        db,
        user=current_user,
        page=summary_page,
        size=summary_size,
        project_id=None,
        q=normalized_query,
    )
    active_plan_statuses = {
        auto_training_plan_service.PlanStatus.ACTIVE.value,
        auto_training_plan_service.PlanStatus.WAITING_FOR_DATA.value,
        auto_training_plan_service.PlanStatus.WAITING_FOR_REVIEW.value,
        auto_training_plan_service.PlanStatus.WAITING_FOR_COVERAGE.value,
        auto_training_plan_service.PlanStatus.ROUND_ACTIVE.value,
    }
    auto_reports: list[dict[str, Any]] = []
    return OkResp[dict[str, Any]](
        data={
            "training_jobs": _training_job_summary(db, current_user=current_user),
            "auto_training": {
                "plan_count": int(auto_plan_total),
                "active_plan_count": sum(
                    1
                    for plan in auto_plans
                    if plan.get("status") in active_plan_statuses
                ),
                "plans": auto_plans,
                "plans_total": int(auto_plan_total),
                "page": summary_page,
                "size": summary_size,
                "round_count": 0,
                "evaluation_reports": [],
                "message": "自动训练计划仅保留历史审计，不参与当前训练入口",
            },
            "reports": {
                "auto": auto_reports,
                "legacy": legacy_reports,
                "legacy_total": int(legacy_total),
                "total": len(auto_reports) + int(legacy_total),
                "page": summary_page,
                "size": summary_size,
            },
            "pagination": {
                "page": summary_page,
                "size": summary_size,
                "plans_total": int(auto_plan_total),
                "reports_total": len(auto_reports) + int(legacy_total),
            },
        }
    )


@router.get(
    "/iteration-preview",
    response_model=OkResp[TrainingIterationPreviewResponse],
    status_code=status.HTTP_200_OK,
)
async def preview_training_iteration(
    task_id: int = Query(..., ge=1),
    source_task_ids: Optional[List[int]] = Query(default=None),
    label_ids: Optional[List[int]] = Query(default=None),
    parent_job_id: Optional[int] = Query(default=None, ge=1),
    training_recipe_key: Optional[str] = Query(default=None, max_length=128),
    training_recipe_version: Optional[int] = Query(default=None, ge=1),
    sample_conditions: Optional[str] = Query(
        default=None,
        max_length=4096,
        description="JSON 对象；维度内为 OR，维度间为 AND",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Resolve the exact parent and AI suggestions before creating a training job."""
    _ = authorization
    selected_task_ids = list(dict.fromkeys(source_task_ids or [task_id]))
    if task_id not in selected_task_ids:
        selected_task_ids.insert(0, task_id)
    tasks = crud_task.visible_query(
        db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(Task.id.in_(selected_task_ids)).all()
    if {int(task.id) for task in tasks} != set(selected_task_ids):
        raise _error("任务不存在或无权查看迭代模型", 403)
    parsed_conditions: dict[str, list[str]] = {}
    if sample_conditions:
        try:
            raw_conditions = json.loads(sample_conditions)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _error("sample_conditions 必须是 JSON 对象") from exc
        if not isinstance(raw_conditions, dict):
            raise _error("sample_conditions 必须是 JSON 对象")
        parsed_conditions = dataset_service.validate_sample_conditions(
            db, raw_conditions
        )
    data = training_iteration_service.build_iteration_preview(
        db,
        current_user=current_user,
        task_id=task_id,
        cmd=CreateYoloTrainJobCommand(
            model_weights="yolo11n.pt",
            weights_source="auto",
            category="yolo_detection",
            training_recipe_key=training_recipe_key,
            training_recipe_version=training_recipe_version,
            sample_conditions=parsed_conditions,
        ),
        source_task_ids=selected_task_ids,
        label_ids=label_ids,
        parent_job_id=parent_job_id,
        sample_conditions=parsed_conditions,
    )
    return OkResp[TrainingIterationPreviewResponse](data=data)


@router.post(
    "/jobs",
    response_model=OkResp[TrainingJobResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_training_job(
    cmd: CreateTrainingJobCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    ensure_seeded(db)
    task = crud_task.get(db=db, task_id=cmd.task_id)
    if (
        task is None
        or getattr(task, "deleted_at", None) is not None
        or not user_has_task_access(current_user, task)
    ):
        raise _error("任务不存在或无权训练", 403)
    requested_dataset_ids = []
    if cmd.dataset_id is not None:
        requested_dataset_ids.append(int(cmd.dataset_id))
    for value in cmd.dataset_ids:
        if int(value) not in requested_dataset_ids:
            requested_dataset_ids.append(int(value))
    task_dataset_id = int(getattr(task, "dataset_id", 0) or 0)
    if requested_dataset_ids and task_dataset_id not in requested_dataset_ids:
        raise _error("任务已不属于所选逻辑数据集范围，请刷新候选任务", 409)
    for dataset_id in requested_dataset_ids:
        try:
            dataset_service._dataset_or_raise(db, dataset_id, current_user)
        except LabelUException as exc:
            raise _error("所选数据集不存在或无权访问", 403) from exc
    # The unified endpoint freezes the immutable snapshot from one source
    # task.  Accepting several logical dataset IDs here would make the
    # persisted scope claim more data than the snapshot actually contains.
    # Multi-dataset training must use the task/label-dimension endpoint,
    # which builds one snapshot from the complete selected task set.
    if len(requested_dataset_ids) > 1:
        raise _error(
            "单任务训练不能合并多个逻辑数据集，请至少选择两个任务后使用合并训练",
            409,
        )
    if (
        cmd.dataset_category_id is not None
        and int(getattr(task, "dataset_category_id", 0) or 0)
        != int(cmd.dataset_category_id)
    ):
        raise _error("任务已不属于所选数据集分类，请刷新候选任务", 409)
    if cmd.dataset_id is not None and int(getattr(task, "dataset_id", 0) or 0) != int(cmd.dataset_id):
        raise _error("任务已不属于所选数据集，请刷新候选任务", 409)
    if cmd.label_ids:
        try:
            annotation_contract.active_labels_by_ids(db, cmd.label_ids)
        except LabelUException as exc:
            raise _error("任务标签筛选参数无效，请刷新标签列表", 400) from exc
        contract = annotation_contract.current_contract(db, int(task.id))
        contract_label_ids: set[int] = set()
        if contract is not None:
            try:
                contract_label_ids = {
                    int(value)
                    for value in _json_list(contract.label_ids_json)
                }
            except (TypeError, ValueError):
                contract_label_ids = set()
        if not contract_label_ids or not set(cmd.label_ids).issubset(
            contract_label_ids
        ):
            raise _error("任务当前标注契约不满足所选标签，请刷新候选任务", 409)
    algorithm_key = cmd.algorithm_key.strip().lower()
    algorithm = crud_algorithm.get_definition(db, algorithm_key)
    if algorithm is None or str(algorithm.status).upper() != "ACTIVE":
        raise _error("算法不存在或未启用", 400)
    try:
        adapter = get_training_adapter(algorithm.training_adapter)
    except ValueError as exc:
        raise _error("算法训练适配器未注册", 501) from exc
    adapter_key = (
        str(getattr(adapter, "key", algorithm.training_adapter) or "").strip().lower()
    )
    if cmd.weights_source == "preset" and cmd.weights_ref_id is not None:
        raise _error("平台预训练权重不需要权重引用 ID", 400)
    if adapter_key != "yolo_detection" and cmd.weights_source != "preset":
        raise _error("当前算法适配器不支持外部或已训练权重引用", 400)
    if (
        adapter_key == "rule_template"
        or str(getattr(algorithm, "algorithm_kind", "") or "").strip().lower()
        == "rule_template"
    ):
        raise _error("规则模板不支持模型训练", 400)
    adapter_mode = (
        str(getattr(adapter, "execution_mode", "in_process") or "in_process")
        .strip()
        .lower()
    )
    requested_mode = str(cmd.execution_mode or "local").strip().lower()
    if requested_mode == "remote" and adapter_key != "yolo_detection":
        raise _error("远程训练当前仅支持 YOLO 训练适配器", 400)
    if requested_mode == "remote" and not (cmd.train_node_id or "").strip():
        raise _error("远程训练必须填写节点 ID", 400)
    if adapter_mode == "container" and requested_mode != "container":
        raise _error("该算法只能通过标准容器 Worker 执行", 400)
    if adapter_mode != "container" and requested_mode == "container":
        raise _error("当前算法不支持标准容器 Worker 执行", 400)
    # Resolve the optional retry key before automatic DatasetVersion creation
    # for every adapter. A replay must not repeat snapshot writes.
    retry_key = normalize_idempotency_key(idempotency_key)
    if retry_key and len(retry_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise _error("Idempotency-Key 不能超过 256 个字符", 400)
    retry_hash = (
        request_payload_hash({"route": "training/jobs", "command": cmd.dict()})
        if retry_key
        else None
    )
    preexisting_job = None
    if retry_key:
        preexisting_job = crud_yolo_train_job.get_by_idempotency(
            db, creator_id=current_user.id, idempotency_key=retry_key
        )
        if preexisting_job is not None and (
            preexisting_job.idempotency_request_hash != retry_hash
        ):
            raise _error("Idempotency-Key 已用于其他训练请求", 409)
        if preexisting_job is not None:
            existing_params = _job_params(preexisting_job)
            # A replay is safe to short-circuit only when the immutable
            # DatasetVersion reference is complete and still points to a
            # frozen row. Older jobs may have been queued before lineage was
            # persisted; those rows are repaired below after resolving the
            # current snapshot.
            if _job_has_complete_dataset_lineage(
                db,
                preexisting_job,
                existing_params,
                expected_task_id=cmd.task_id,
                expected_dataset_id=getattr(task, "dataset_id", None),
                expected_dataset_version_id=cmd.dataset_version_id,
                expected_job_type=adapter_key,
            ):
                _requeue_pending_training_job(
                    db,
                    preexisting_job,
                    adapter_key=adapter_key,
                )
                return OkResp[TrainingJobResponse](
                    data=_response(
                        preexisting_job,
                        algorithm_key=algorithm.algorithm_key,
                        adapter_key=algorithm.training_adapter,
                        dataset_version_id=_job_dataset_version_id(
                            preexisting_job, existing_params
                        ),
                        params=existing_params,
                    )
                )
            if _job_has_conflicting_dataset_lineage(
                db,
                preexisting_job,
                existing_params,
                expected_task_id=cmd.task_id,
                expected_dataset_id=getattr(task, "dataset_id", None),
                expected_dataset_version_id=cmd.dataset_version_id,
                expected_job_type=adapter_key,
            ):
                raise _error("训练资源血缘已变化，请刷新候选任务后重试", 409)
    if cmd.dataset_version_id is None:
        # The normal 7GAI path starts from a completed annotation task. Keep
        # the immutable DatasetVersion lineage, but never create a logical
        # dataset here; the task must already be bound to a user-managed one.
        generated = await dataset_service.ensure_training_version_for_task(
            db,
            task=task,
            current_user=current_user,
            selected_label_ids=[int(value) for value in cmd.label_ids],
            sample_conditions=cmd.sample_conditions,
        )
        version = crud_dataset.get_version(db, int(generated.id))
    else:
        version = crud_dataset.get_version(db, cmd.dataset_version_id)
    dataset = None
    if version is not None:
        dataset = (
            db.query(Dataset)
            .filter(Dataset.id == int(version.dataset_id))
            .one_or_none()
        )
    if version is None or dataset is None:
        raise _error("数据集版本不存在", 400)
    try:
        dataset_service._dataset_or_raise(db, int(version.dataset_id), current_user)
    except LabelUException as exc:
        raise _error("数据集版本不存在或无权访问", 403) from exc
    if str(version.status).upper() != "FROZEN":
        raise _error("只有已冻结的数据集版本可以训练", 409)
    if cmd.dataset_id is not None and int(version.dataset_id) != int(cmd.dataset_id):
        raise _error("训练数据版本与所选数据集不一致", 409)
    if requested_dataset_ids and int(version.dataset_id) not in requested_dataset_ids:
        raise _error("训练数据版本与所选数据集范围不一致", 409)
    frozen_label_ids: set[int] = set()
    try:
        snapshot_items = _json_list(getattr(version, "labels_snapshot_json", None))
        frozen_label_ids = {
            int(item.get("label_id"))
            for item in snapshot_items
            if isinstance(item, dict) and item.get("label_id")
        }
    except (TypeError, ValueError):
        frozen_label_ids = set()
    if not frozen_label_ids:
        try:
            version_metadata = json.loads(version.metadata_json or "{}")
            frozen_label_ids = {
                int(value)
                for value in (
                    version_metadata.get("label_ids", [])
                    if isinstance(version_metadata, dict)
                    else []
                )
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            frozen_label_ids = set()
    if cmd.label_ids and frozen_label_ids and not set(cmd.label_ids).issubset(
        frozen_label_ids
    ):
        raise _error("训练标签必须来自冻结数据集版本的标签契约", 409)
    supported_chips = _json_list(getattr(algorithm, "supported_chips_json", None))
    if cmd.chip_type and supported_chips:
        requested_chip = cmd.chip_type.strip().lower()
        allowed_chips = {str(item).strip().lower() for item in supported_chips}
        if requested_chip not in allowed_chips:
            raise _error(f"算法不支持目标芯片: {cmd.chip_type.strip()}", 400)
    if (
        getattr(adapter, "execution_mode", "in_process") == "container"
        and not (cmd.container_image or "").strip()
    ):
        raise _error("容器型算法必须配置 allowlisted container_image", 400)

    recipe = _training_recipe(db, cmd.training_recipe_key, cmd.training_recipe_version)
    if cmd.training_recipe_key and recipe is None:
        raise _error("训练配方不存在或已停用", 400)
    recipe_job_type = str(getattr(recipe, "job_type", "") or "").strip().lower()
    allowed_recipe_job_types = {
        algorithm_key,
        str(algorithm.training_adapter or "").strip().lower(),
    }
    # The dedicated plate workers are reached through the existing YOLO
    # adapter.  Keep this exception narrow so unrelated algorithms cannot
    # claim a plate recipe merely by naming it in the request.
    if adapter_key == "yolo_detection":
        allowed_recipe_job_types.update({"plate_detection", "plate_recognition"})
    if recipe is not None and recipe_job_type not in allowed_recipe_job_types:
        raise _error("训练配方与算法能力不匹配", 400)
    cmd = _normalize_plate_training_contract(cmd, recipe)

    # The immutable dataset snapshot is the backend authority for global-tag
    # filters.  Normalize/validate once here and use the same values for the
    # branch contract, persisted job, and worker payload.
    backend_conditions = dataset_service.validate_sample_conditions(
        db, cmd.sample_conditions
    )
    contract_cmd = cmd.copy(update={"sample_conditions": backend_conditions})
    supplied_contract_scope = (
        cmd.selection_contract.get("dataset_scope")
        if isinstance(cmd.selection_contract, dict)
        else None
    )
    supplied_dataset_version_id = (
        supplied_contract_scope.get("dataset_version_id")
        if isinstance(supplied_contract_scope, dict)
        else None
    )
    if supplied_dataset_version_id in (None, "", 0) and isinstance(
        cmd.selection_contract, dict
    ):
        # Accept the pre-v1 top-level spelling for old clients.
        supplied_dataset_version_id = cmd.selection_contract.get("dataset_version_id")
    if not isinstance(cmd.selection_contract, dict) or supplied_dataset_version_id in (
        None,
        "",
        0,
    ):
        # A legacy preview may not yet know the immutable version.  The final
        # version is authoritative at create time, so do not reject its old
        # provisional branch hash.
        contract_cmd = contract_cmd.copy(update={"branch_key": None})

    params = dict(cmd.params or {})
    if cmd.training_recipe_key:
        params["training_recipe_key"] = canonical_training_recipe_key(
            cmd.training_recipe_key
        )
    if params.get("runtime_profile"):
        params["runtime_profile"] = canonical_runtime_profile(
            params.get("runtime_profile")
        )
    params.update(
        {
            "algorithm_key": algorithm.algorithm_key,
            "algorithm_id": int(algorithm.id),
            "dataset_version_id": int(version.id),
            "chip_type": cmd.chip_type.lower() if cmd.chip_type else None,
            "precision": cmd.precision,
            # Preserve the exact frozen manifest metadata in the job record.  A
            # worker must not re-resolve a mutable dataset path after enqueue.
            "dataset_manifest": {
                "dataset_version_id": int(version.id),
                "dataset_id": int(version.dataset_id),
                "dataset_ids": requested_dataset_ids or [int(version.dataset_id)],
                "version_no": int(version.version_no),
                "manifest_rel_path": version.manifest_rel_path,
                "manifest_sha256": version.manifest_sha256,
                "label_set_signature": version.label_set_signature,
                "class_order_signature": version.class_order_signature,
                "dataset_label_contract_id": getattr(
                    version, "dataset_label_contract_id", None
                ),
                "dataset_contract_revision": getattr(
                    version, "dataset_contract_revision", None
                ),
                "labels_snapshot_json": getattr(
                    version, "labels_snapshot_json", None
                ),
                "sample_count": int(version.sample_count or 0),
                "frozen_at": version.frozen_at,
            },
            "selection_filter": {
                "dataset_ids": requested_dataset_ids or [int(version.dataset_id)],
                "dataset_category_id": (
                    int(cmd.dataset_category_id)
                    if cmd.dataset_category_id is not None
                    else None
                ),
                "label_ids": [int(value) for value in cmd.label_ids],
            },
        }
    )
    # Persist a canonical branch contract alongside the mutable request
    # parameters.  The frozen dataset version and its class snapshot are the
    # server authority; client-provided contract extensions are retained.
    manifest_labels = _lineage_class_names(params.get("dataset_manifest") or {})
    contract, contract_digest, contract_text = _unified_selection_contract(
        contract_cmd,
        dataset_id=int(version.dataset_id),
        dataset_version_id=int(version.id),
        task_ids=[int(cmd.task_id)],
        class_order=manifest_labels,
        label_set_signature_value=(
            str(getattr(version, "label_set_signature", "") or "").strip() or None
        ),
        recipe_key=(recipe.recipe_key if recipe is not None else cmd.training_recipe_key),
        recipe_version=(
            int(recipe.version)
            if recipe is not None
            else cmd.training_recipe_version
        ),
    )
    lineage_parent_training_job_id = cmd.parent_training_job_id
    if lineage_parent_training_job_id is None and str(cmd.weights_source or "").lower() == "trained":
        lineage_parent_training_job_id = cmd.weights_ref_id
    lineage = model_series_service.resolve_model_lineage(
        db,
        current_user=current_user,
        selection_contract=contract,
        branch_key=contract_cmd.branch_key,
        series_id=cmd.series_id,
        variant_id=cmd.variant_id,
        parent_model_version_id=cmd.parent_model_version_id,
        parent_training_job_id=lineage_parent_training_job_id,
        inheritance_mode=cmd.inheritance_mode,
        series_name=cmd.series_name,
        variant_name=cmd.variant_name,
        model_family=(
            cmd.model_family
            or str(getattr(recipe, "model_family", "") or "").strip()
            or algorithm_key
        ),
    )
    # The legacy YOLO adapter starts its own transaction and intentionally
    # rolls back any SQLAlchemy autobegin state.  Commit the small lineage
    # catalog transaction here so the IDs survive that adapter boundary.
    db.commit()
    params.update(
        {
            "selection_contract": contract,
            "branch_key": lineage.branch_key,
            "selection_contract_sha256": lineage.selection_contract_sha256,
            "sample_conditions": backend_conditions,
            "series_id": lineage.series_id,
            "variant_id": lineage.variant_id,
            "series_name": lineage.series.name,
            "variant_name": lineage.variant.name,
            "model_family": lineage.series.model_family,
            "parent_model_version_id": lineage.parent_model_version_id,
            "parent_training_job_id": lineage.parent_training_job_id,
            "inheritance_mode": lineage.inheritance_mode,
        }
    )
    if cmd.weights_source != "preset" or cmd.weights_ref_id is not None:
        params["weights_source"] = cmd.weights_source
        params["weights_ref_id"] = cmd.weights_ref_id
    if recipe is not None:
        params.update(
            {
                "training_recipe_key": recipe.recipe_key,
                "training_recipe_version": int(recipe.version),
                "model_family": recipe.model_family,
                "annotation_kind": recipe.annotation_kind,
                "recipe_component_contract": _recipe_json(
                    recipe.component_contract_json
                ),
                # Store defaults as a snapshot; explicit request params remain
                # authoritative and are never silently overwritten.
                "recipe_default_hyperparams": _recipe_json(
                    recipe.default_hyperparams_json
                ),
            }
        )
    if cmd.container_image:
        params["container_image"] = cmd.container_image.strip()

    # Keep the existing YOLO execution path as a compatibility adapter.  The
    # immutable dataset binding remains in hyperparams for lineage materialization.
    if adapter_key == "yolo_detection":
        legacy_cmd = CreateYoloTrainJobCommand(
            model_weights=cmd.model_weights or "yolo11n.pt",
            weights_source=cmd.weights_source,
            weights_ref_id=cmd.weights_ref_id,
            plate_training_target=cmd.plate_training_target,
            rect_preset_slug=cmd.rect_preset_slug,
            epochs=cmd.epochs,
            imgsz=cmd.imgsz,
            category=algorithm.algorithm_key,
            hyperparams=params,
            training_recipe_key=recipe.recipe_key if recipe else None,
            training_recipe_version=int(recipe.version) if recipe else None,
            execution_mode=requested_mode,
            train_node_id=(cmd.train_node_id or "").strip() or None,
            series_id=lineage.series_id,
            variant_id=lineage.variant_id,
            parent_model_version_id=lineage.parent_model_version_id,
            parent_training_job_id=lineage.parent_training_job_id,
            branch_key=lineage.branch_key,
            selection_contract=contract,
            sample_conditions=backend_conditions,
            inheritance_mode=lineage.inheritance_mode,
        )
        legacy = await yolo_train_service.create_train_job(
            db,
            cmd.task_id,
            legacy_cmd,
            current_user,
            idempotency_key=idempotency_key,
            idempotency_request_hash=retry_hash,
            # The unified route writes dataset_manifest_json after the
            # compatibility service creates the row.  Queue only after that
            # write has committed (see the dispatch below).
            defer_dispatch=True,
        )
        job = crud_yolo_train_job.get(db, int(legacy.id))
        if job is None:
            # The compatibility service returning an id for a missing row is a
            # contract violation; surface it instead of returning a misleading
            # successful response.
            raise _error("YOLO 训练作业创建后无法读取", 500)
        with begin_transaction(db):
            job = _persist_unified_yolo_lineage(
                db,
                job,
                params=params,
            )
        status_value = getattr(job, "status", "")
        status_value = getattr(status_value, "value", status_value)
        # A repaired idempotent row may already be PROCESSING/terminal.  In
        # that case updating its lineage is enough; starting it again would
        # create a second worker for the same idempotency key.  PENDING rows
        # are safe to hand to the queue, which deduplicates in-memory retries
        # and lets the persistent watchdog recover after a process restart.
        if str(status_value or "").strip().upper() in {"", "PENDING"}:
            persisted_mode = str(
                getattr(job, "execution_mode", None) or "local"
            ).strip().lower()
            dispatch_cmd = legacy_cmd.copy(
                update={
                    "execution_mode": (
                        "remote" if persisted_mode == "remote" else "local"
                    ),
                    "train_node_id": (
                        getattr(job, "train_node_id", None)
                        if persisted_mode == "remote"
                        else None
                    ),
                }
            )
            yolo_train_service._submit_yolo_train_job(
                int(job.id), int(job.task_id), dispatch_cmd
            )
        return OkResp[TrainingJobResponse](
            data=_response(
                job,
                algorithm_key=algorithm.algorithm_key,
                adapter_key=algorithm.training_adapter,
                dataset_version_id=version.id,
                params=params,
            )
        )

    replayed_job = None
    with begin_transaction(db):
        if retry_key:
            existing = crud_yolo_train_job.get_by_idempotency(
                db, creator_id=current_user.id, idempotency_key=retry_key
            )
            if existing is not None:
                if existing.idempotency_request_hash != retry_hash:
                    raise _error("Idempotency-Key 已用于其他训练请求", 409)
                existing = _persist_unified_yolo_lineage(
                    db,
                    existing,
                    params=params,
                )
                status_value = getattr(existing, "status", "")
                status_value = getattr(status_value, "value", status_value)
                if str(status_value or "").strip().upper() == "PENDING":
                    # The first attempt may have committed the row but failed
                    # before publishing its worker event.  The event key is
                    # deterministic per job, so retrying the enqueue is
                    # idempotent and repairs that durable handoff.
                    enqueue_training_worker_command(db, job=existing)
                return OkResp[TrainingJobResponse](
                    data=_response(
                        existing,
                        algorithm_key=algorithm.algorithm_key,
                        adapter_key=algorithm.training_adapter,
                        dataset_version_id=version.id,
                        params=params,
                    )
                )
        try:
            with db.begin_nested():
                job = crud_yolo_train_job.create(
                    db=db,
                    task_id=cmd.task_id,
                    user_id=current_user.id,
                    project_id=None,
                    model_weights=cmd.model_weights
                    or algorithm.default_recipe
                    or algorithm.algorithm_key,
                    epochs=cmd.epochs,
                    imgsz=cmd.imgsz,
                    category=algorithm.algorithm_key,
                    hyperparams_json=json.dumps(
                        params,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                    dataset_manifest_json=json.dumps(
                        params["dataset_manifest"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                    execution_mode=adapter_mode,
                    job_type=algorithm.training_adapter,
                    idempotency_key=retry_key,
                    idempotency_request_hash=retry_hash,
                    series_id=lineage.series_id,
                    variant_id=lineage.variant_id,
                    parent_model_version_id=lineage.parent_model_version_id,
                    parent_training_job_id=lineage.parent_training_job_id,
                    branch_key=lineage.branch_key,
                    selection_contract_sha256=lineage.selection_contract_sha256,
                    selection_contract_json=contract_text,
                    sample_conditions_json=json.dumps(
                        backend_conditions,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    inheritance_mode=lineage.inheritance_mode,
                )
        except IntegrityError:
            if not retry_key:
                raise
            replayed_job = crud_yolo_train_job.get_by_idempotency(
                db, creator_id=current_user.id, idempotency_key=retry_key
            )
            if replayed_job is None:
                raise
            if replayed_job.idempotency_request_hash != retry_hash:
                raise _error("Idempotency-Key 已用于其他训练请求", 409)
            job = replayed_job
        if replayed_job is not None:
            replayed_job = _persist_unified_yolo_lineage(
                db,
                replayed_job,
                params=params,
            )
            status_value = getattr(replayed_job, "status", "")
            status_value = getattr(status_value, "value", status_value)
            if str(status_value or "").strip().upper() == "PENDING":
                enqueue_training_worker_command(db, job=replayed_job)
            return OkResp[TrainingJobResponse](
                data=_response(
                    replayed_job,
                    algorithm_key=algorithm.algorithm_key,
                    adapter_key=algorithm.training_adapter,
                    dataset_version_id=version.id,
                    params=params,
                )
            )
        if recipe is not None:
            job.training_recipe_key = recipe.recipe_key
            job.training_recipe_version = int(recipe.version)
            db.flush()
        enqueue_training_worker_command(db, job=job)
    return OkResp[TrainingJobResponse](
        data=_response(
            job,
            algorithm_key=algorithm.algorithm_key,
            adapter_key=algorithm.training_adapter,
            dataset_version_id=version.id,
            params=params,
        )
    )


@router.get("/jobs", response_model=OkRespWithMeta[List[TrainingJobResponse]])
async def list_training_jobs(
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按作业、任务、数据集、权重、状态或执行节点搜索",
    ),
    legacy_search: Optional[str] = Query(
        None,
        alias="search",
        max_length=128,
        include_in_schema=False,
        description="旧版搜索参数；q 有值时优先使用 q",
    ),
    status_value: Optional[str] = Query(None, alias="status"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """List training jobs using the task access contract."""
    _ = authorization
    normalized_search = str(search if search is not None else legacy_search or "").strip() or None
    rows, total = crud_yolo_train_job.list_jobs_for_user(
        db,
        current_user.id,
        search=normalized_search,
        status=status_value,
        page=page,
        size=size,
        task_access_unrestricted=is_task_superuser(current_user),
        task_access_builtin_admin=is_consistent_pool_admin(current_user),
    )
    data: list[TrainingJobResponse] = []
    for job, task in rows:
        params = _job_params(job)
        manifest = params.get("dataset_manifest")
        if not isinstance(manifest, dict):
            try:
                manifest = json.loads(
                    getattr(job, "dataset_manifest_json", None) or "{}"
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                manifest = {}
            if isinstance(manifest, dict) and manifest.get("dataset_version_id"):
                params.setdefault(
                    "dataset_version_id", int(manifest["dataset_version_id"])
                )
        algorithm_key = str(
            params.get("algorithm_key")
            or getattr(job, "category", None)
            or getattr(job, "job_type", None)
            or ""
        )
        dataset_version_id = params.get("dataset_version_id")
        try:
            dataset_version_id = int(dataset_version_id) if dataset_version_id else None
        except (TypeError, ValueError):
            dataset_version_id = None
        data.append(
            _response(
                job,
                algorithm_key=algorithm_key,
                adapter_key=str(getattr(job, "job_type", "") or ""),
                dataset_version_id=dataset_version_id,
                params=params,
                task_name=getattr(task, "name", None),
            )
        )
    return OkRespWithMeta[List[TrainingJobResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


@router.get("/jobs/{job_id}/history", response_model=OkResp[TrainingHistoryResponse])
async def get_training_job_history(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return bounded per-epoch YOLO metrics for the detail modal only."""
    _ = authorization
    job, _task = _accessible_job(db, job_id, current_user)
    raw_status = getattr(job, "status", "")
    status_value = getattr(raw_status, "value", raw_status)
    target_raw = getattr(job, "epochs", None)
    try:
        target_epochs = max(1, int(float(target_raw))) if target_raw is not None else None
    except (TypeError, ValueError):
        target_epochs = None

    train_dir = resolve_train_dir_from_weights(getattr(job, "weights_path", None))
    if train_dir is None:
        candidate = train_job_work_train_dir(int(job.id))
        train_dir = candidate if candidate.is_dir() else None
    history = parse_results_csv_history(train_dir) if train_dir is not None else []

    best_epoch = None
    if history:
        best = max(
            history,
            key=lambda item: (
                float(item.get("fitness"))
                if item.get("fitness") is not None
                else float(item.get("map50_95") or -1.0),
                float(item.get("map50_95") or -1.0),
                int(item.get("epoch") or -1),
            ),
        )
        best_epoch = best.get("epoch")
    return OkResp[TrainingHistoryResponse](
        data=TrainingHistoryResponse(
            job_id=int(job.id),
            available=bool(history),
            message=None if history else "当前作业没有保存逐轮训练记录",
            source="train/results.csv" if history else None,
            status=str(status_value or ""),
            target_epochs=target_epochs,
            current_epoch=history[-1].get("epoch") if history else None,
            best_epoch=best_epoch,
            latest=history[-1] if history else None,
            history=history,
        )
    )


@router.get("/jobs/{job_id}", response_model=OkResp[TrainingJobResponse])
async def get_training_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    job, task = _accessible_job(db, job_id, current_user)
    response = _job_response(db, job, task)
    # The unified list/detail contract historically returned raw metrics but
    # omitted the canonical quality assessment produced by the YOLO service.
    # That made a completed job with valid mAP/precision/recall appear as
    # "待验证" in the detail page.  Reuse the existing assessment calculation
    # here; do not duplicate threshold logic in the frontend.
    quality = yolo_train_service.get_train_job_by_id(
        db=db, job_id=int(job.id), current_user=current_user
    )
    for field_name in (
        "training_assessment",
        "edge_validation_assessment",
        "release_gate_assessment",
        "quality_assessment",
        "bm1688_export_id",
        "bm1688_export_status",
        "bm1688_compile_status",
        "bm1688_compile_status_label",
        "bm1688_compile_updated_at",
        "bm1688_bmodel_rel_path",
    ):
        value = getattr(quality, field_name, None)
        if value is not None and hasattr(value, "dict"):
            value = value.dict(exclude_none=True)
        setattr(response, field_name, value)
    exports = crud_yolo_bm1688_export.list_all_for_job(
        db, yolo_train_job_id=int(job.id)
    )
    if exports:
        response.bm1688_compile_error = getattr(exports[0], "error_message", None)
    return OkResp[TrainingJobResponse](data=response)


@router.post("/jobs/{job_id}/pause", response_model=OkResp[TrainingJobResponse])
async def pause_training_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    job, task = _accessible_job(db, job_id, current_user)
    yolo_train_service.pause_train_job(
        db=db, task_id=int(task.id), job_id=int(job.id), current_user=current_user
    )
    updated = crud_yolo_train_job.get(db, job.id) or job
    return OkResp[TrainingJobResponse](data=_job_response(db, updated, task))


@router.post("/jobs/{job_id}/restart", response_model=OkResp[TrainingJobResponse])
async def restart_training_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    job, task = _accessible_job(db, job_id, current_user)
    await yolo_train_service.restart_train_job(
        db=db, task_id=int(task.id), job_id=int(job.id), current_user=current_user
    )
    updated = crud_yolo_train_job.get(db, job.id) or job
    return OkResp[TrainingJobResponse](data=_job_response(db, updated, task))
