"""同系列训练权重继承与相邻版本权威验证指标对比。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from fastapi import status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_model_version,
    crud_task,
    crud_yolo_eval_report,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.response.training import (
    TrainingIterationParentResponse,
    TrainingIterationPreviewResponse,
    TrainingVersionComparisonMetric,
    TrainingVersionComparisonResponse,
)
from labelu.internal.application.service import annotation_contract
from labelu.internal.application.service.model_series import series_variant_names
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_contract import (
    class_order_signature,
    label_set_signature,
)
from labelu.internal.common.training_contract import (
    TRAINING_RECIPE_ALIASES,
    branch_identity_sha256,
    canonical_selection_contract,
    canonical_training_recipe_key,
    selection_contract_sha256 as build_selection_contract_sha256,
)
from labelu.internal.common.training_sample_conditions import normalize_sample_conditions
from labelu.internal.common.task_access import user_has_task_access
from labelu.internal.common.yolo_training_metrics import metrics_contract_from_summary
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_METRICS = (
    ("map50_95", "mAP50-95"),
    ("map50", "mAP50"),
    ("precision", "精确率"),
    ("recall", "召回率"),
)


def _try_resolve_weights_path(stored: str | None) -> Path | None:
    """Resolve one persisted checkpoint through the canonical YOLO resolver.

    Iteration candidates are historical rows, so a deleted checkpoint must be
    treated as an ineligible candidate instead of making the whole preview
    fail.  The import stays lazy because ``yolo_train`` imports this module in
    its create path.
    """

    if not str(stored or "").strip():
        return None
    try:
        from labelu.internal.application.service.yolo_train import (
            resolve_weights_path,
        )

        resolved = resolve_weights_path(stored)
    except (LabelUException, OSError, TypeError, ValueError):
        return None
    try:
        return resolved if resolved.is_file() else None
    except OSError:
        return None


def _raise_missing_parent_weights() -> None:
    """Raise the stable conflict returned when a selected parent is stale."""

    raise LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=status.HTTP_409_CONFLICT,
        message="所选模型的权重文件不存在或已不可用，请重新选择模型",
    )


def _authoritative_metrics(job: YoloTrainJob) -> dict[str, Any]:
    contract = metrics_contract_from_summary(getattr(job, "metrics_summary", None))
    metrics = contract.get("authoritative_val_metrics") or {}
    return metrics if isinstance(metrics, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _task_contract_signatures(
    db: Session, task_id: int
) -> tuple[str | None, str | None]:
    contract = annotation_contract.current_contract(db, int(task_id))
    if contract is None:
        return None, None
    return (
        str(getattr(contract, "label_set_signature", "") or "").strip() or None,
        str(getattr(contract, "class_order_signature", "") or "").strip() or None,
    )


@dataclass(frozen=True)
class _IterationSelection:
    """Internal selection result used to keep preview and create decisions aligned."""

    command: CreateYoloTrainJobCommand
    parent: YoloTrainJob | None = None
    selection_policy: str = "preset"
    selection_scope: str = "none"
    candidate_count: int = 0
    fallback_reason: str | None = None


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _job_sample_conditions(job: YoloTrainJob) -> dict[str, list[str]]:
    """Read the immutable global-tag filter from current and legacy jobs."""

    direct = _json_object(getattr(job, "sample_conditions_json", None))
    if direct:
        return normalize_sample_conditions(direct)
    params = _json_object(getattr(job, "hyperparams_json", None))
    nested = params.get("sample_conditions")
    if isinstance(nested, dict) and nested:
        return normalize_sample_conditions(nested)
    contract = _json_object(getattr(job, "selection_contract_json", None))
    raw_conditions = contract.get("global_conditions")
    if isinstance(raw_conditions, list):
        extracted: dict[str, list[str]] = {}
        for item in raw_conditions:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension") or "").strip()
            values = item.get("values")
            if dimension and isinstance(values, (list, tuple, set)):
                extracted[dimension] = [str(value) for value in values]
        return normalize_sample_conditions(extracted)
    return {}


def _recipe_lookup_keys(value: Any) -> tuple[str, ...]:
    """Return canonical and legacy spellings accepted for one recipe key."""

    normalized = str(value or "").strip()
    if not normalized:
        return ()
    canonical = canonical_training_recipe_key(normalized)
    keys = {normalized, canonical}
    keys.update(
        alias
        for alias, target in TRAINING_RECIPE_ALIASES.items()
        if target == canonical
    )
    return tuple(sorted(key for key in keys if key))


def _recipe_fields_from_object(raw: Any) -> tuple[str | None, int | None]:
    """Read recipe lineage from one persisted JSON object."""

    if not isinstance(raw, dict):
        return None, None
    key = None
    for field in ("training_recipe_key", "recipe_key"):
        value = str(raw.get(field) or "").strip()
        if value:
            key = value
            break
    nested = raw.get("training_recipe")
    if key is None and isinstance(nested, str):
        key = nested.strip() or None
    if key is None and isinstance(nested, dict):
        for field in ("training_recipe_key", "recipe_key", "key"):
            value = str(nested.get(field) or "").strip()
            if value:
                key = value
                break
    version = None
    for field in ("training_recipe_version", "recipe_version"):
        version = _optional_int(raw.get(field))
        if version is not None:
            break
    if version is None and isinstance(nested, dict):
        for field in ("training_recipe_version", "recipe_version", "version"):
            version = _optional_int(nested.get(field))
            if version is not None:
                break
    return key, version


def _job_recipe_contract(
    job: YoloTrainJob,
) -> tuple[str | None, int | None]:
    """Resolve recipe lineage from columns and legacy nested snapshots."""

    top_key = str(getattr(job, "training_recipe_key", None) or "").strip() or None
    top_version = _optional_int(getattr(job, "training_recipe_version", None))
    params = _json_object(getattr(job, "hyperparams_json", None))
    manifests: list[dict[str, Any]] = []
    for raw in (
        getattr(job, "dataset_manifest_json", None),
        params.get("dataset_manifest"),
    ):
        manifest = _json_object(raw)
        if manifest and manifest not in manifests:
            manifests.append(manifest)

    key = top_key
    version = top_version
    for source in (params, *manifests):
        nested_key, nested_version = _recipe_fields_from_object(source)
        key = key or nested_key
        version = version or nested_version
        if key and version:
            break
    return key, version


def _optional_int(value: Any) -> int | None:
    """Read persisted numeric lineage fields without leaking parse errors."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _non_empty_signature(value: Any) -> str | None:
    """Normalize a persisted contract signature without accepting blanks."""

    normalized = str(value or "").strip()
    return normalized or None


def _job_contract_signatures(
    db: Session, job: YoloTrainJob
) -> tuple[str | None, str | None]:
    """Resolve label signatures across current and legacy job lineage fields.

    Older YOLO jobs persisted the immutable dataset manifest only inside
    ``hyperparams_json.dataset_manifest`` and left the denormalized job columns
    empty. Iteration compatibility must read that frozen lineage before
    consulting the mutable task contract; otherwise valid historical models
    are incorrectly hidden from the same-series selector.
    """

    label_signature = _non_empty_signature(
        getattr(job, "label_set_signature", None)
    )
    order_signature = _non_empty_signature(
        getattr(job, "class_order_signature", None)
    )
    params = _json_object(getattr(job, "hyperparams_json", None))
    manifest_candidates: list[dict[str, Any]] = []
    for raw in (
        getattr(job, "dataset_manifest_json", None),
        params.get("dataset_manifest"),
    ):
        manifest = _json_object(raw)
        if manifest and manifest not in manifest_candidates:
            manifest_candidates.append(manifest)
    for manifest in manifest_candidates:
        label_signature = label_signature or _non_empty_signature(
            manifest.get("label_set_signature")
        )
        order_signature = order_signature or _non_empty_signature(
            manifest.get("class_order_signature")
        )
        if label_signature and order_signature:
            break

    # A few early unified jobs stored only the dataset-version reference. The
    # frozen version remains authoritative and is safer than the live task
    # contract, which may have changed after the job was trained.
    if not (label_signature and order_signature):
        dataset_version_id = None
        for manifest in manifest_candidates:
            dataset_version_id = _optional_int(
                manifest.get("dataset_version_id")
            )
            if dataset_version_id:
                break
        if dataset_version_id:
            version = (
                db.query(DatasetVersion)
                .filter(DatasetVersion.id == int(dataset_version_id))
                .one_or_none()
            )
            if version is not None:
                label_signature = label_signature or _non_empty_signature(
                    getattr(version, "label_set_signature", None)
                )
                order_signature = order_signature or _non_empty_signature(
                    getattr(version, "class_order_signature", None)
                )
    return label_signature, order_signature


def _source_task_ids_for_job(job: YoloTrainJob) -> list[int]:
    raw = getattr(job, "source_task_ids_json", None)
    if raw:
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                result = []
                for value in values:
                    try:
                        task_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if task_id > 0 and task_id not in result:
                        result.append(task_id)
                if result:
                    return result
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    task_id = getattr(job, "task_id", None)
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return []
    return [task_id] if task_id > 0 else []


def _resolve_auto_iteration_selection(
    db: Session,
    *,
    current_user: User,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    source_task_ids: list[int] | None = None,
    expected_label_set_signature: str | None = None,
    expected_class_order_signature: str | None = None,
) -> _IterationSelection:
    """Resolve auto weights and retain the evidence used for that decision.

    The public resolver historically returned only a command.  Keeping the
    richer result private lets existing create paths remain source-compatible
    while the preview endpoint can explain exactly which candidate won.
    """
    if cmd.weights_source != "auto":
        return _IterationSelection(command=cmd)

    if expected_label_set_signature is None or expected_class_order_signature is None:
        task_label_signature, task_order_signature = _task_contract_signatures(
            db, task_id
        )
        expected_label_set_signature = (
            expected_label_set_signature or task_label_signature
        )
        expected_class_order_signature = (
            expected_class_order_signature or task_order_signature
        )

    has_full_contract = bool(
        expected_label_set_signature and expected_class_order_signature
    )
    allowed_task_ids = {int(value) for value in (source_task_ids or [task_id])}
    query = db.query(YoloTrainJob).filter(
        YoloTrainJob.job_type == "yolo_detection",
        YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
        YoloTrainJob.weights_path.isnot(None),
        YoloTrainJob.weights_path != "",
    )
    if not has_full_contract:
        query = query.filter(YoloTrainJob.task_id == int(task_id))
    requested_recipe_key = canonical_training_recipe_key(cmd.training_recipe_key)
    recipe_lookup_keys = _recipe_lookup_keys(requested_recipe_key)
    if recipe_lookup_keys:
        # Keep rows with an empty denormalized key in the scan.  Older jobs
        # carried the recipe only inside hyperparams/dataset_manifest and are
        # resolved authoritatively below from that immutable snapshot.
        query = query.filter(
            or_(
                YoloTrainJob.training_recipe_key.in_(recipe_lookup_keys),
                YoloTrainJob.training_recipe_key.is_(None),
                YoloTrainJob.training_recipe_key == "",
            )
        )
        if cmd.training_recipe_version is not None:
            # Version may also be present only in a nested legacy manifest;
            # apply that part of the compatibility gate in Python below.
            query = query.filter(
                or_(
                    YoloTrainJob.training_recipe_version
                    == int(cmd.training_recipe_version),
                    YoloTrainJob.training_recipe_version.is_(None),
                )
            )

    # Iterate the complete ordered result set in bounded ORM batches.  A hard
    # row limit can hide an older compatible parent whenever newer jobs are
    # failed, stale, or from another label contract.
    candidates = (
        query.order_by(
            YoloTrainJob.task_id.in_(list(allowed_task_ids)).desc(),
            YoloTrainJob.id.desc(),
        )
        .yield_per(200)
    )
    compatible: list[tuple[YoloTrainJob, bool]] = []
    requested_conditions = normalize_sample_conditions(cmd.sample_conditions)
    task_access_cache: dict[int, bool] = {}
    for candidate in candidates:
        # ``weights_path`` being non-empty is only a database hint.  The
        # canonical resolver additionally enforces MEDIA_ROOT containment and
        # verifies that the checkpoint still exists on disk.
        if _try_resolve_weights_path(candidate.weights_path) is None:
            continue
        candidate_recipe_key, candidate_recipe_version = _job_recipe_contract(
            candidate
        )
        if recipe_lookup_keys:
            if (
                not candidate_recipe_key
                or canonical_training_recipe_key(candidate_recipe_key)
                != requested_recipe_key
            ):
                continue
            if (
                cmd.training_recipe_version is not None
                and candidate_recipe_version != int(cmd.training_recipe_version)
            ):
                continue
        if has_full_contract:
            candidate_label_signature, candidate_order_signature = (
                _job_contract_signatures(db, candidate)
            )
            if (
                candidate_label_signature != expected_label_set_signature
                or candidate_order_signature != expected_class_order_signature
            ):
                continue
        # ``category`` is a user-facing series name on canonical training
        # tasks, while the preview route uses ``yolo_detection`` as the job
        # type default.  Treat the algorithm key as already covered by the
        # job_type predicate; only an explicit custom category narrows it.
        requested_category = str(cmd.category or "").strip()
        if (
            requested_category
            and requested_category
            not in {
                "yolo_detection",
                "yolo",
            }
            and str(candidate.category or "") not in {"", requested_category}
        ):
            continue
        # ``is_task_superuser`` is not a sufficient bypass here: configured
        # service/superuser identities must still be denied administrator-only
        # consistent-pool tasks.  Centralize that distinction in the shared
        # task-access policy for every candidate, including ordinary tasks.
        candidate_task_id = int(candidate.task_id)
        if candidate_task_id not in task_access_cache:
            source_task = crud_task.get(db=db, task_id=candidate_task_id)
            task_access_cache[candidate_task_id] = bool(
                source_task is not None
                and user_has_task_access(current_user, source_task)
            )
        if not task_access_cache[candidate_task_id]:
            continue
        # Prefer a parent trained with the same global-tag subset.  If no
        # exact match exists, retain the historical compatible fallback so an
        # optional filter does not make an otherwise valid iteration unusable.
        compatible.append((candidate, _job_sample_conditions(candidate) == requested_conditions))

    if compatible:
        compatible.sort(key=lambda item: not item[1])
        candidate = compatible[0][0]
        candidate_task_id = int(getattr(candidate, "task_id", 0) or 0)
        if not has_full_contract:
            selection_scope = "primary_task"
        elif candidate_task_id in allowed_task_ids:
            selection_scope = "selected_tasks"
        else:
            selection_scope = "cross_task_fallback"
        return _IterationSelection(
            command=cmd.copy(
                update={
                    "weights_source": "trained",
                    "weights_ref_id": int(candidate.id),
                    "model_weights": f"trained:{int(candidate.id)}",
                }
            ),
            parent=candidate,
            selection_policy="auto_prefer_selected_tasks_then_latest",
            selection_scope=selection_scope,
            candidate_count=len(compatible),
        )

    return _IterationSelection(
        command=cmd.copy(
            update={
                "weights_source": "preset",
                "weights_ref_id": None,
                "model_weights": str(cmd.model_weights or "").strip() or "yolo11n.pt",
            }
        ),
        selection_policy="auto_compatible_parent",
        selection_scope="selected_tasks" if has_full_contract else "primary_task",
        candidate_count=0,
        fallback_reason="no_compatible_completed_parent",
    )


def resolve_auto_iteration_weights(
    db: Session,
    *,
    current_user: User,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    source_task_ids: list[int] | None = None,
    expected_label_set_signature: str | None = None,
    expected_class_order_signature: str | None = None,
) -> CreateYoloTrainJobCommand:
    """将显式 auto 来源冻结为一个真实父作业，找不到兼容版本时安全回退。"""
    return _resolve_auto_iteration_selection(
        db,
        current_user=current_user,
        task_id=task_id,
        cmd=cmd,
        source_task_ids=source_task_ids,
        expected_label_set_signature=expected_label_set_signature,
        expected_class_order_signature=expected_class_order_signature,
    ).command


def validate_explicit_iteration_parent(
    db: Session,
    *,
    current_user: User,
    parent_job_id: int,
    task_id: int | None = None,
    requested_recipe_key: str | None = None,
    requested_recipe_version: int | None = None,
    expected_label_set_signature: str | None = None,
    expected_class_order_signature: str | None = None,
) -> YoloTrainJob:
    """Validate the exact parent used by an explicit iteration request.

    The preview endpoint and the create services must share this gate.  The
    weight resolver still owns the final file/path check; this helper handles
    lineage semantics that require the target training contract.
    """
    if task_id is not None and (
        expected_label_set_signature is None or expected_class_order_signature is None
    ):
        task_label_signature, task_order_signature = _task_contract_signatures(
            db, int(task_id)
        )
        expected_label_set_signature = (
            expected_label_set_signature or task_label_signature
        )
        expected_class_order_signature = (
            expected_class_order_signature or task_order_signature
        )
    parent = crud_yolo_train_job.get(db=db, job_id=int(parent_job_id))
    parent_task = (
        crud_task.get(db=db, task_id=int(parent.task_id))
        if parent is not None and parent.task_id is not None
        else None
    )
    if (
        parent is None
        or parent.status != YoloTrainStatus.COMPLETED.value
        or str(getattr(parent, "job_type", "") or "").strip().lower()
        != "yolo_detection"
        or parent_task is None
        or not user_has_task_access(current_user, parent_task)
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="所选模型不可用于迭代训练，请刷新模型列表",
        )

    # Do this check before lineage comparison so the preview never presents a
    # parent that submission cannot actually load.  ``weights_path`` can be a
    # stale relative path, an escaped path, or a file deleted after training;
    # all are a 409 conflict with the user's selected parent.
    if _try_resolve_weights_path(parent.weights_path) is None:
        _raise_missing_parent_weights()

    normalized_recipe_key = canonical_training_recipe_key(requested_recipe_key)
    parent_recipe_key, parent_recipe_version = _job_recipe_contract(parent)
    if normalized_recipe_key and (
        not parent_recipe_key
        or canonical_training_recipe_key(parent_recipe_key)
        != normalized_recipe_key
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="所选模型的训练配方与当前训练配方不兼容，请重新选择模型",
        )
    if requested_recipe_version is not None and parent_recipe_version != int(
        requested_recipe_version
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="所选模型的训练配方版本与当前训练配方不兼容，请重新选择模型",
        )
    parent_label_signature, parent_order_signature = _job_contract_signatures(
        db, parent
    )
    if expected_label_set_signature and (
        parent_label_signature != expected_label_set_signature
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="所选模型的标签集合与当前训练任务不兼容，请重新选择模型",
        )
    if expected_class_order_signature and (
        parent_order_signature != expected_class_order_signature
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="所选模型的标签顺序与当前训练任务不兼容，请重新选择模型",
        )
    return parent


def build_iteration_preview(
    db: Session,
    *,
    current_user: User,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    source_task_ids: list[int] | None = None,
    label_ids: list[int] | None = None,
    parent_job_id: int | None = None,
    expected_label_set_signature: str | None = None,
    expected_class_order_signature: str | None = None,
    sample_conditions: dict[str, list[str]] | None = None,
) -> TrainingIterationPreviewResponse:
    """预览创建时真正使用的父模型及其最佳 AI 下一轮建议，不写数据库。"""
    requested_sample_conditions = normalize_sample_conditions(
        sample_conditions if sample_conditions is not None else cmd.sample_conditions
    )
    if requested_sample_conditions != normalize_sample_conditions(cmd.sample_conditions):
        cmd = cmd.copy(update={"sample_conditions": requested_sample_conditions})
    if label_ids and (
        expected_label_set_signature is None or expected_class_order_signature is None
    ):
        labels = annotation_contract.active_labels_by_ids(db, label_ids)
        expected_label_set_signature = (
            expected_label_set_signature
            or label_set_signature([int(row.id) for row in labels])
        )
        expected_class_order_signature = (
            expected_class_order_signature
            or class_order_signature([str(row.label_value) for row in labels])
        )
    elif expected_label_set_signature is None or expected_class_order_signature is None:
        # Task-dimension previews do not send label_ids because the complete
        # contract comes from the selected task.  Use that contract for the
        # explicit-parent compatibility gate as well.
        task_label_signature, task_order_signature = _task_contract_signatures(
            db, task_id
        )
        expected_label_set_signature = (
            expected_label_set_signature or task_label_signature
        )
        expected_class_order_signature = (
            expected_class_order_signature or task_order_signature
        )

    explicit_parent_id = parent_job_id
    if explicit_parent_id is None and cmd.weights_source == "trained":
        explicit_parent_id = cmd.weights_ref_id

    if explicit_parent_id is None:
        selection = _resolve_auto_iteration_selection(
            db,
            current_user=current_user,
            task_id=task_id,
            cmd=cmd,
            source_task_ids=source_task_ids,
            expected_label_set_signature=expected_label_set_signature,
            expected_class_order_signature=expected_class_order_signature,
        )
        resolved = selection.command
    else:
        parent = validate_explicit_iteration_parent(
            db,
            current_user=current_user,
            parent_job_id=int(explicit_parent_id),
            task_id=task_id,
            requested_recipe_key=cmd.training_recipe_key,
            requested_recipe_version=cmd.training_recipe_version,
            expected_label_set_signature=expected_label_set_signature,
            expected_class_order_signature=expected_class_order_signature,
        )

        resolved = cmd.copy(
            update={
                "weights_source": "trained",
                "weights_ref_id": int(parent.id),
                "model_weights": f"trained:{int(parent.id)}",
            }
        )
        selection = _IterationSelection(
            command=resolved,
            parent=parent,
            selection_policy="explicit_parent",
            selection_scope="explicit_parent",
            candidate_count=1,
        )

    if resolved.weights_source != "trained" or resolved.weights_ref_id is None:
        return TrainingIterationPreviewResponse(
            weights_source="preset",
            model_weights=resolved.model_weights or "yolo11n.pt",
            selection_policy=selection.selection_policy,
            selection_scope=selection.selection_scope,
            candidate_count=selection.candidate_count,
            fallback_reason=selection.fallback_reason,
            sample_conditions=requested_sample_conditions,
        )

    parent = selection.parent or crud_yolo_train_job.get(
        db=db, job_id=int(resolved.weights_ref_id)
    )
    if parent is None:
        return TrainingIterationPreviewResponse(
            weights_source="preset",
            model_weights="yolo11n.pt",
            selection_policy=selection.selection_policy,
            selection_scope=selection.selection_scope,
            candidate_count=selection.candidate_count,
            fallback_reason="resolved_parent_missing",
            sample_conditions=requested_sample_conditions,
        )
    parent_task = crud_task.get(db=db, task_id=int(parent.task_id))
    model = crud_model_version.get_by_training_job(db, int(parent.id))
    model_display_name = str(getattr(model, "display_name", "") or "").strip()
    display_name = model_display_name or str(
        getattr(parent_task, "name", None) or f"训练模型 #{int(parent.id)}"
    )
    job_status = getattr(parent, "status", None)
    job_status = getattr(job_status, "value", job_status)
    params = _json_object(getattr(parent, "hyperparams_json", None))
    manifest = _json_object(getattr(parent, "dataset_manifest_json", None))
    nested_manifest = _json_object(params.get("dataset_manifest"))
    # Legacy jobs can have a partially denormalized top-level manifest. Keep
    # those authoritative values, while filling missing snapshot fields from
    # the immutable manifest retained in hyperparams.
    for key, value in nested_manifest.items():
        manifest.setdefault(key, value)

    # ``dataset_version_no`` was added to the response projection before the
    # ORM model gained a matching column.  Keep the preview compatible with
    # both historical model mappings and newer deployments.
    model_dataset_version_no = (
        _optional_int(getattr(model, "dataset_version_no", None))
        if model is not None
        else None
    )
    dataset_version_no = model_dataset_version_no or _optional_int(
        manifest.get("version_no")
        or manifest.get("dataset_version_no")
        or params.get("dataset_version_no")
    )
    model_dataset_version_id = (
        _optional_int(getattr(model, "dataset_version_id", None))
        if model is not None
        else None
    )
    frozen_version = None
    if model_dataset_version_no is None and model_dataset_version_id:
        frozen_version = (
            db.query(DatasetVersion)
            .filter(DatasetVersion.id == int(model_dataset_version_id))
            .one_or_none()
        )
        model_dataset_version_no = _optional_int(
            getattr(frozen_version, "version_no", None)
        )
    dataset_version_id = model_dataset_version_id or _optional_int(
        params.get("dataset_version_id") or manifest.get("dataset_version_id")
    )

    # A parent job historically did not persist the logical dataset directly.
    # Prefer the task/model lineage when available, then recover it from the
    # immutable dataset version so the UI can identify the exact source.
    parent_dataset_id = (
        _optional_int(getattr(model, "dataset_id", None))
        if model is not None
        else None
    )
    parent_dataset_id = parent_dataset_id or _optional_int(
        getattr(parent_task, "dataset_id", None)
    )
    parent_dataset = getattr(parent_task, "dataset", None)
    parent_dataset_name = (
        str(getattr(parent_dataset, "name", "") or "").strip() or None
    )
    if dataset_version_id and (frozen_version is None or parent_dataset_id is None):
        frozen_version = (
            db.query(DatasetVersion)
            .filter(DatasetVersion.id == int(dataset_version_id))
            .one_or_none()
        )
    if parent_dataset_id is None and frozen_version is not None:
        parent_dataset_id = _optional_int(getattr(frozen_version, "dataset_id", None))
    if parent_dataset_name is None and parent_dataset_id is not None:
        parent_dataset = (
            db.query(Dataset)
            .filter(Dataset.id == int(parent_dataset_id))
            .one_or_none()
        )
        parent_dataset_name = (
            str(getattr(parent_dataset, "name", "") or "").strip() or None
        )

    from labelu.internal.application.service.yolo_train_auto_iteration import (
        _inner_from_report_row,
        pick_best_next_iteration_report,
    )

    reports = crud_yolo_eval_report.list_by_job_and_kind(
        db, job_id=int(parent.id), report_kind="next_iteration"
    )
    report = pick_best_next_iteration_report(reports)
    suggested_params = _inner_from_report_row(report) if report is not None else {}
    suggested_params = {
        key: value
        for key, value in suggested_params.items()
        if key not in {"model_weights", "device", "notes"}
        and value is not None
        and value != ""
    }
    parent_label_signature, parent_order_signature = _job_contract_signatures(
        db, parent
    )
    parent_recipe_key, parent_recipe_version = _job_recipe_contract(parent)
    parent_series_id = _optional_int(getattr(parent, "series_id", None))
    parent_variant_id = _optional_int(getattr(parent, "variant_id", None))
    if parent_series_id is None and model is not None:
        parent_series_id = _optional_int(getattr(model, "series_id", None))
    if parent_variant_id is None and model is not None:
        parent_variant_id = _optional_int(getattr(model, "variant_id", None))
    parent_series_name, parent_variant_name = series_variant_names(
        db, parent_series_id, parent_variant_id
    )
    parent_model_family = None
    try:
        from labelu.internal.domain.models.model_series import ModelSeries

        if parent_series_id is not None:
            series_row = (
                db.query(ModelSeries)
                .filter(ModelSeries.id == int(parent_series_id))
                .one_or_none()
            )
            parent_model_family = (
                str(getattr(series_row, "model_family", "") or "").strip() or None
            )
    except (AttributeError, TypeError, ValueError):
        # Keep historical iteration previews usable when the optional series
        # tables are not present yet.
        parent_model_family = None
    parent_contract = _json_object(getattr(parent, "selection_contract_json", None))
    if not parent_contract and model is not None:
        parent_contract = _json_object(
            getattr(model, "selection_contract_json", None)
        )
    if not parent_contract and model is not None:
        parent_contract = _json_object(getattr(model, "manifest_json", None)).get(
            "selection_contract", {}
        )
    parent_branch_key = (
        str(getattr(parent, "branch_key", "") or "").strip()
        or (
            str(getattr(model, "branch_key", "") or "").strip()
            if model is not None
            else ""
        )
        or None
    )
    parent_selection_contract_sha256 = (
        str(getattr(parent, "selection_contract_sha256", "") or "").strip()
        or (
            str(getattr(model, "selection_contract_sha256", "") or "").strip()
            if model is not None
            else ""
        )
        or None
    )
    if parent_selection_contract_sha256 is None and isinstance(parent_contract, dict):
        try:
            parent_selection_contract_sha256 = build_selection_contract_sha256(
                canonical_selection_contract(parent_contract)
            )
        except (TypeError, ValueError):
            parent_selection_contract_sha256 = None
    if parent_branch_key is None:
        if isinstance(parent_contract, dict):
            try:
                parent_branch_key = branch_identity_sha256(parent_contract)
            except (TypeError, ValueError):
                parent_branch_key = parent_selection_contract_sha256
        else:
            parent_branch_key = parent_selection_contract_sha256
    return TrainingIterationPreviewResponse(
        weights_source="trained",
        weights_ref_id=int(parent.id),
        model_weights=f"trained:{int(parent.id)}",
        selection_policy=selection.selection_policy,
        selection_scope=selection.selection_scope,
        candidate_count=selection.candidate_count,
        fallback_reason=selection.fallback_reason,
        sample_conditions=requested_sample_conditions,
        parent=TrainingIterationParentResponse(
            job_id=int(parent.id),
            task_id=int(parent.task_id),
            task_name=getattr(parent_task, "name", None),
            model_version_id=int(model.id) if model is not None else None,
            version_no=int(model.version_no) if model is not None else None,
            branch_version_no=(
                _optional_int(getattr(model, "branch_version_no", None))
                if model is not None
                else None
            ),
            display_name=display_name,
            dataset_id=parent_dataset_id,
            dataset_name=parent_dataset_name,
            dataset_version_no=dataset_version_no,
            dataset_version_id=dataset_version_id,
            dataset_content_signature=(
                str(parent.dataset_content_signature)
                if parent.dataset_content_signature
                else (
                    str(manifest.get("dataset_content_signature"))
                    if manifest.get("dataset_content_signature")
                    else None
                )
            ),
            weights_filename=Path(str(parent.weights_path or "best.pt")).name
            or "best.pt",
            created_at=parent.created_at,
            job_status=str(job_status) if job_status else None,
            job_type=str(parent.job_type) if parent.job_type else None,
            category=str(parent.category) if parent.category else None,
            training_recipe_key=parent_recipe_key,
            training_recipe_version=parent_recipe_version,
            label_set_signature=parent_label_signature,
            class_order_signature=parent_order_signature,
            source_task_ids=_source_task_ids_for_job(parent),
            model_key=(
                str(model.model_key) if model is not None and model.model_key else None
            ),
            model_status=(
                str(model.status) if model is not None and model.status else None
            ),
            series_id=parent_series_id,
            series_name=parent_series_name,
            model_family=parent_model_family,
            variant_id=parent_variant_id,
            variant_name=parent_variant_name,
            branch_key=parent_branch_key,
            selection_contract_sha256=parent_selection_contract_sha256,
            selection_contract=parent_contract if isinstance(parent_contract, dict) else None,
        ),
        ai_report_id=int(report.id) if report is not None else None,
        ai_provider_key=report.provider_key if report is not None else None,
        suggested_params=suggested_params,
    )


def build_previous_version_comparison(
    db: Session, job: YoloTrainJob
) -> TrainingVersionComparisonResponse:
    """只与 model_weights 中真实记录的父版本比较，不使用历史最佳值替代。"""
    match = re.fullmatch(r"trained:(\d+)", str(getattr(job, "model_weights", "") or ""))
    previous = (
        db.query(YoloTrainJob)
        .filter(YoloTrainJob.id == int(match.group(1)))
        .one_or_none()
        if match
        else None
    )
    if previous is None:
        return TrainingVersionComparisonResponse(
            available=False,
            status="no_previous_version",
            current_job_id=int(job.id),
            summary="这是基础权重训练的首个版本，没有上一版可对比。",
            comparison_basis="authoritative_val_metrics",
        )

    base = {
        "available": True,
        "current_job_id": int(job.id),
        "previous_job_id": int(previous.id),
        "previous_created_at": previous.created_at,
        "comparison_basis": "authoritative_val_metrics",
    }
    current_metrics = _authoritative_metrics(job)
    previous_metrics = _authoritative_metrics(previous)
    rows = [
        TrainingVersionComparisonMetric(
            key=key,
            label=label,
            previous=_number(previous_metrics.get(key)),
            current=_number(current_metrics.get(key)),
            delta=(
                _number(current_metrics.get(key)) - _number(previous_metrics.get(key))
                if _number(current_metrics.get(key)) is not None
                and _number(previous_metrics.get(key)) is not None
                else None
            ),
        )
        for key, label in _METRICS
    ]
    primary = next(
        (row for row in rows if row.key == "map50_95" and row.delta is not None), None
    )
    if primary is None:
        primary = next(
            (row for row in rows if row.key == "map50" and row.delta is not None), None
        )
    if primary is None:
        return TrainingVersionComparisonResponse(
            **base,
            status="insufficient_metrics",
            metrics=rows,
            summary="两版缺少可对比的权威验证 mAP 指标，暂不判断提升或下降。",
        )

    comparable = [row for row in rows if row.delta is not None]
    increased = any((row.delta or 0) > 1e-9 for row in comparable)
    decreased = any((row.delta or 0) < -1e-9 for row in comparable)
    primary_delta = float(primary.delta or 0)
    if abs(primary_delta) <= 1e-9 and not increased and not decreased:
        result = "unchanged"
        summary = f"主指标 {primary.label} 与上一版持平，其余已记录指标也没有明显变化。"
    elif primary_delta > 1e-9 and not decreased:
        result = "improved"
        summary = f"主指标 {primary.label} 提升 {primary_delta * 100:.2f} 个百分点，其余已记录指标未下降。"
    elif primary_delta < -1e-9 and not increased:
        result = "regressed"
        summary = f"主指标 {primary.label} 下降 {abs(primary_delta) * 100:.2f} 个百分点，其余已记录指标未提升。"
    else:
        result = "mixed"
        direction = (
            "提升"
            if primary_delta > 1e-9
            else "下降" if primary_delta < -1e-9 else "持平"
        )
        summary = f"主指标 {primary.label}{direction}，其他指标存在取舍，建议结合精确率与召回率复核。"

    current_dataset = str(getattr(job, "dataset_content_signature", "") or "").strip()
    previous_dataset = str(
        getattr(previous, "dataset_content_signature", "") or ""
    ).strip()
    same_dataset = (
        current_dataset == previous_dataset
        if current_dataset and previous_dataset
        else None
    )
    if same_dataset is False:
        summary += " 两版数据快照不同，结果仅供迭代参考。"
    return TrainingVersionComparisonResponse(
        **base,
        status=result,
        metrics=rows,
        summary=summary,
        same_dataset=same_dataset,
    )
