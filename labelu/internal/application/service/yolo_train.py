from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import json
import multiprocessing as mp
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from fastapi import UploadFile, status
from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_annotation_taxonomy,
    crud_task,
    crud_training_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_board_validation,
    crud_yolo_eval_report,
    crud_yolo_edge_validation_job,
    crud_yolo_imported_model,
    crud_yolo_train_job,
)
from labelu.internal.adapter.persistence import crud_strategy_task_integration
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.response.yolo_train import (
    TrainingAssessmentResponse,
    YoloModelValidationDetectionResponse,
    YoloModelValidateImageResponse,
    YoloClassMetricRow,
    YoloClassMetricsDetail,
    YoloImportedModelRowResponse,
    YoloTrainJobListRowResponse,
    YoloTrainJobResponse,
    YoloTrainStopInfoResponse,
    YoloTestValMetrics,
    YoloTrainedModelCatalogSummary,
    YoloTrainingValMetrics,
    YoloTrainedModelRowResponse,
    YoloValTestComparison,
    YoloValidationPassAssessment,
)
from labelu.internal.common.optional_dependencies import ensure_ultralytics_installed
from labelu.internal.common.execution_fencing import execution_lease_is_active
from labelu.internal.common.label_contract import (
    annotation_sha256 as canonical_annotation_sha256,
    class_order_signature,
)
from labelu.internal.application.service.yolo_dataset import (
    DATASET_VALIDATION_STRICT,
    RUN_DATASET_VALIDATION_AFTER_EXPORT,
    build_yolo_rect_class_names,
    count_class_instances_in_dataset,
    prepare_yolo_dataset,
)
from labelu.internal.application.service.yolo_dataset_validation import (
    validate_exported_yolo_dataset,
)
from labelu.internal.application.service import training_task as training_task_service
from labelu.internal.application.service import model_series as model_series_service
from labelu.internal.application.service.yolo_train_merge import (
    load_merged_training_context,
    merge_task_label,
    parse_source_task_ids,
    resolve_source_task_ids_for_create,
    source_task_ids_json,
)
from labelu.internal.application.service.yolo_training import (
    lifecycle as yolo_lifecycle,
)
from labelu.internal.application.service.yolo_training import (
    hyperparams as yolo_hyperparams,
)
from labelu.internal.application.service.yolo_training import resume as yolo_resume
from labelu.internal.application.service.yolo_training import runtime as yolo_runtime
from labelu.internal.application.service.yolo_training import summary as yolo_summary
from labelu.internal.application.service.yolo_training import (
    plate_summary as yolo_plate_summary,
)
from labelu.internal.application.service.yolo_quality_assessment import (
    build_assessment_payload,
    quality_policy_snapshot_for_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.phase_one_contract import ensure_phase_one_training_job_type
from labelu.internal.common.training_contract import (
    branch_identity_sha256,
    build_selection_contract,
    canonical_training_recipe_key,
    canonical_selection_contract,
    selection_contract_json,
    selection_contract_sha256,
)
from labelu.internal.common.storage import get_storage_backend
from labelu.internal.common.yolo_train_hyperparams import (
    default_train_patience,
    normalize_hyperparams,
    normalize_runtime_editable_hyperparams,
)
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.idempotency import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    normalize_idempotency_key,
    request_payload_hash,
)
from labelu.internal.common.yolo_training_metrics import (
    build_class_metrics_detail,
    build_val_test_comparison,
    dataset_counts_from_metrics_summary,
    resolve_job_sample_counts,
    final_metrics_from_metrics_summary,
    format_training_metrics_line,
    metrics_contract_from_summary,
    parse_results_csv_best_metrics,
    parse_results_csv_final_metrics,
    read_last_epoch_from_results_csv,
    resolve_train_dir_from_weights,
    training_stop_info,
    read_test_val_metrics_from_summary,
    validation_pass_assessment,
)
from labelu.internal.common.yolo_ultralytics_split_val import (
    merge_test_val_into_metrics_summary,
    merge_val_per_class_into_metrics_summary,
    read_class_names_from_data_yaml,
    run_split_val,
)


def _frozen_manifest_samples_for_job(job: Any) -> list[dict[str, Any]] | None:
    """Load and verify the immutable sample records for a unified job.

    The unified route stores the DatasetVersion manifest reference in
    ``hyperparams_json.dataset_manifest``.  The worker must use that manifest
    as the source of truth instead of re-querying every DONE sample from the
    mutable task.
    """
    expected_sha256 = ""
    has_manifest_reference = False
    try:
        params = json.loads(getattr(job, "hyperparams_json", None) or "{}")
        manifest_ref = (
            params.get("dataset_manifest") if isinstance(params, dict) else None
        )
        has_manifest_reference = isinstance(manifest_ref, dict) and bool(
            str(manifest_ref.get("manifest_rel_path") or "").strip()
        )
        manifest_rel_path = (
            manifest_ref.get("manifest_rel_path")
            if isinstance(manifest_ref, dict)
            else None
        )
        if not manifest_rel_path:
            return None
        expected_sha256 = str(manifest_ref.get("manifest_sha256") or "").strip().lower()
        raw = get_storage_backend().read_text(str(manifest_rel_path))
        if expected_sha256:
            actual_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if actual_sha256 != expected_sha256:
                raise RuntimeError("冻结数据集清单校验失败: manifest_sha256 不匹配")
        manifest = json.loads(raw)
        samples = manifest.get("samples") if isinstance(manifest, dict) else None
        if not isinstance(samples, list):
            if expected_sha256:
                raise RuntimeError("冻结数据集清单缺少 samples，禁止回退任务数据")
            return None
        normalized_samples = [
            item
            for item in samples
            if isinstance(item, dict) and item.get("sample_id") is not None
        ]
        if expected_sha256 and not normalized_samples:
            raise RuntimeError("冻结数据集清单没有可训练样本，禁止回退任务数据")
        return normalized_samples
    except RuntimeError:
        raise
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        # Legacy jobs and externally managed manifests may not be readable by
        # the local worker.  Preserve their compatibility behavior; new
        # unified jobs always carry a local DatasetVersion manifest and its
        # digest, so a read/parse failure there must fail closed instead of
        # silently re-resolving mutable task samples.
        if has_manifest_reference and expected_sha256:
            raise RuntimeError("冻结数据集清单不可读，禁止回退任务数据")
        return None


def _frozen_sample_ids_for_job(job: Any) -> set[int]:
    samples = _frozen_manifest_samples_for_job(job)
    if not samples:
        return set()
    return {int(item["sample_id"]) for item in samples}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_frozen_sample_content(
    samples: Iterable[Any], manifest_samples: list[dict[str, Any]]
) -> None:
    """Reject mutable image/annotation content after DatasetVersion freeze."""
    expected_by_id = {int(item["sample_id"]): item for item in manifest_samples}
    for sample in samples:
        sample_id = int(getattr(sample, "id", 0) or 0)
        expected = expected_by_id.get(sample_id)
        if expected is None:
            raise RuntimeError(f"冻结数据集清单缺少样本 {sample_id}，禁止训练")

        try:
            current_annotation = json.loads(getattr(sample, "data", None) or "null")
        except (TypeError, ValueError):
            current_annotation = getattr(sample, "data", None)
        expected_annotation_sha = (
            str(
                expected.get("source_annotation_sha256")
                or expected.get("annotation_sha256")
                or ""
            )
            .strip()
            .lower()
        )
        if not expected_annotation_sha:
            expected_annotation = expected.get("annotation")
            if expected_annotation is None:
                raise RuntimeError(f"冻结样本 {sample_id} 缺少标注摘要，禁止训练")
            expected_annotation_sha = canonical_annotation_sha256(expected_annotation)
        if canonical_annotation_sha256(current_annotation) != expected_annotation_sha:
            raise RuntimeError(f"冻结样本 {sample_id} 的标注内容已变化，禁止训练")

        expected_asset_sha = str(expected.get("asset_sha256") or "").strip().lower()
        attachment = getattr(sample, "file", None)
        current_asset_sha = (
            str(getattr(attachment, "sha256", None) or "").strip().lower()
        )
        asset_path = str(getattr(attachment, "path", None) or "").strip()
        local_path = (
            get_storage_backend().get_local_path(asset_path) if asset_path else None
        )
        if local_path and local_path.is_file():
            current_asset_sha = _file_sha256(local_path)
        if not expected_asset_sha:
            raise RuntimeError(f"冻结样本 {sample_id} 缺少图片摘要，禁止训练")
        if not current_asset_sha or current_asset_sha != expected_asset_sha:
            raise RuntimeError(f"冻结样本 {sample_id} 的图片内容已变化，禁止训练")


from labelu.internal.common.model_asset_manifest import build_dataset_manifest
from labelu.internal.common.strategy_task_marker import strategy_task_marker
from labelu.internal.common.task_access import (
    assert_user_has_task_access,
    is_consistent_pool_admin,
    is_task_superuser,
)
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.annotation_taxonomy import (
    DatasetCategory,
    TrainingRecipe,
)
from labelu.internal.domain.models.model_version import ModelArtifact, ModelVersion
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_imported_model import YoloImportedModel
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def _selected_class_names_for_job(job: Any) -> list[str] | None:
    """Read the immutable class subset captured with a unified job."""
    try:
        params = json.loads(getattr(job, "hyperparams_json", None) or "{}")
        manifest_ref = (
            params.get("dataset_manifest") if isinstance(params, dict) else None
        )
        raw_snapshot = (
            manifest_ref.get("labels_snapshot_json")
            if isinstance(manifest_ref, dict)
            else None
        )
        snapshot = (
            json.loads(raw_snapshot or "[]")
            if isinstance(raw_snapshot, str)
            else raw_snapshot
        )
        if not isinstance(snapshot, list):
            return None
        names = [
            str(item.get("label_value") or item.get("label_key") or "").strip()
            for item in snapshot
            if isinstance(item, dict)
            and str(item.get("label_value") or item.get("label_key") or "").strip()
        ]
        return names or None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _assert_task_access(task, user: User) -> None:
    assert_user_has_task_access(task, user)


def _strategy_task_marker_for_train_job(
    db: Session | None, job_id: int
) -> dict[str, str] | None:
    if db is None:
        return None
    row = crud_strategy_task_integration.get_by_derived_train_job_id(
        db, derived_train_job_id=int(job_id)
    )
    return strategy_task_marker(row)


def _parse_task_config(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _preset_label_items_for_config(db: Session, task_config: dict) -> list | None:
    slug = str((task_config or {}).get("defaultRectPresetId") or "").strip()
    if not slug:
        return None
    preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
    if not preset:
        return None
    return list(preset.items or [])


def _preset_label_items_for_training_task(
    db: Session, training_task_id: int
) -> list | None:
    row = crud_training_task.get(db, training_task_id=int(training_task_id))
    if not row:
        return None
    preset_slugs: list[str] = []
    for preset_snap in (
        crud_training_task.parse_snapshot(row.selected_presets_snapshot_json) or []
    ):
        slug = str(
            preset_snap.get("preset_slug") or preset_snap.get("slug") or ""
        ).strip()
        if slug and slug not in preset_slugs:
            preset_slugs.append(slug)
    if not preset_slugs:
        for item in (
            crud_training_task.parse_snapshot(row.selected_label_items_snapshot_json)
            or []
        ):
            slug = str(item.get("preset_slug") or "").strip()
            if slug and slug not in preset_slugs:
                preset_slugs.append(slug)
    if not preset_slugs:
        return None
    items: list = []
    for slug in preset_slugs:
        preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
        if preset and preset.items:
            items.extend(list(preset.items))
    return items or None


def _training_task_display_subtitle(
    training_task_row, fallback_task_name: str | None
) -> str | None:
    if training_task_row is None:
        return None
    source_tasks = crud_training_task.parse_snapshot(
        training_task_row.source_tasks_snapshot_json
    )
    source_task_count = len(source_tasks)
    primary_task_name = (
        str(fallback_task_name or "").strip()
        or str(getattr(training_task_row, "name", "") or "").strip()
        or None
    )
    if not primary_task_name:
        return None
    if source_task_count <= 1:
        return primary_task_name
    return f"{primary_task_name} 等 {source_task_count - 1} 个任务"


def _training_task_response_fields(
    db: Session,
    job: YoloTrainJob,
    fallback_task_name: str | None,
    *,
    include_detail: bool = False,
) -> dict[str, Any]:
    training_task_id = getattr(job, "training_task_id", None)
    if not training_task_id or db is None:
        return {
            "training_task_id": None,
            "training_task_name": None,
            "training_task_mode": None,
            "training_task_display_subtitle": None,
            "training_task_detail": None,
            **_dataset_category_response_fields(db, job),
        }
    training_task_row = crud_training_task.get(
        db,
        training_task_id=int(training_task_id),
    )
    if training_task_row is None:
        return {
            "training_task_id": int(training_task_id),
            "training_task_name": None,
            "training_task_mode": None,
            "training_task_display_subtitle": None,
            "training_task_detail": None,
            **_dataset_category_response_fields(db, job),
        }
    return {
        "training_task_id": training_task_row.id,
        "training_task_name": training_task_row.name,
        "training_task_mode": training_task_row.mode,
        "training_task_display_subtitle": _training_task_display_subtitle(
            training_task_row,
            fallback_task_name,
        ),
        "training_task_detail": (
            training_task_service.build_training_task_detail_response(
                db,
                training_task_row,
            )
            if include_detail
            else None
        ),
        **_dataset_category_response_fields(db, job, training_task_row),
    }


def _dataset_category_response_fields(
    db: Session | None,
    job: YoloTrainJob,
    training_task_row: Any | None = None,
) -> dict[str, Any]:
    category_id = getattr(job, "dataset_category_id", None)
    snapshot: dict[str, Any] = {}
    if training_task_row is not None:
        parsed = crud_training_task.parse_snapshot(
            getattr(training_task_row, "dataset_category_snapshot_json", None)
        )
        if isinstance(parsed, dict):
            snapshot = parsed
            category_id = category_id or parsed.get("id")

    category_key = str(snapshot.get("category_key") or "").strip() or None
    category_name = str(snapshot.get("name") or "").strip() or None
    if (
        category_id is not None
        and db is not None
        and (not category_key or not category_name)
    ):
        category = (
            db.query(DatasetCategory)
            .filter(DatasetCategory.id == int(category_id))
            .first()
        )
        if category is not None:
            category_key = (
                category_key or str(category.category_key or "").strip() or None
            )
            category_name = category_name or str(category.name or "").strip() or None
    return {
        "dataset_category_id": int(category_id) if category_id is not None else None,
        "dataset_category_key": category_key,
        "dataset_category_name": category_name,
    }


def _job_contract_response_fields(job: YoloTrainJob) -> dict[str, Any]:
    try:
        parsed_names = json.loads(getattr(job, "class_names_json", None) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed_names = []
    class_names = (
        [str(value) for value in parsed_names] if isinstance(parsed_names, list) else []
    )
    # Dedicated plate jobs use a fixed runtime class, but older rows were
    # created outside the generic label-contract flow. Expose that stable
    # class contract to model-library and publish consumers without inventing
    # a label-library ID signature.
    if str(getattr(job, "job_type", "") or "").strip() in {
        "plate_detection",
        "plate_recognition",
    }:
        class_names = ["doubleEP"]
        order_signature = class_order_signature(class_names)
    else:
        order_signature = (
            str(getattr(job, "class_order_signature", None) or "").strip() or None
        )
    try:
        selection_contract = json.loads(
            getattr(job, "selection_contract_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        selection_contract = None
    if not isinstance(selection_contract, dict):
        selection_contract = None
    try:
        sample_conditions = json.loads(
            getattr(job, "sample_conditions_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        sample_conditions = {}
    if not isinstance(sample_conditions, dict):
        sample_conditions = {}
    return {
        "class_names": class_names,
        "label_set_signature": (
            str(getattr(job, "label_set_signature", None) or "").strip() or None
        ),
        "class_order_signature": order_signature,
        "data_yaml_sha256": (
            str(getattr(job, "data_yaml_sha256", None) or "").strip() or None
        ),
        "dataset_content_signature": (
            str(getattr(job, "dataset_content_signature", None) or "").strip() or None
        ),
        "training_recipe_key": (
            str(getattr(job, "training_recipe_key", None) or "").strip() or None
        ),
        "training_recipe_version": getattr(job, "training_recipe_version", None),
        "runtime_profile": (
            str(getattr(job, "runtime_profile", None) or "").strip() or None
        ),
        "runtime_interface_version": getattr(job, "runtime_interface_version", None),
        "series_id": getattr(job, "series_id", None),
        "variant_id": getattr(job, "variant_id", None),
        "branch_key": (
            str(getattr(job, "branch_key", None) or "").strip() or None
        ),
        "selection_contract_sha256": (
            str(getattr(job, "selection_contract_sha256", None) or "").strip()
            or None
        ),
        "selection_contract": selection_contract,
        "sample_conditions": sample_conditions,
        "parent_model_version_id": getattr(job, "parent_model_version_id", None),
        "parent_training_job_id": getattr(job, "parent_training_job_id", None),
        "inheritance_mode": (
            str(getattr(job, "inheritance_mode", None) or "").strip() or None
        ),
    }


def _job_work_root(job_id: int) -> Path:
    return yolo_runtime.job_work_root(
        settings.MEDIA_ROOT,
        settings.YOLO_TRAIN_WORK_SUBDIR,
        job_id,
    )


_TRAIN_PHASE_LABELS: dict[str, str] = {
    "preparing_dataset": "正在准备训练数据集",
    "dataset_ready": "数据集已就绪，即将加载模型",
    "loading_model": "正在加载预训练权重（首次可能需从网络下载，请耐心等待）",
    "running": "Ultralytics 训练中",
    "restarting": "正在应用临时超参并续训",
    "completed": "训练已完成",
    "failed": "训练失败",
    "cancelled": "已暂停",
}

_ACTIVE_TRAIN_JOBS: set[int] = set()
_TRAIN_SIGTERM_HOOKS_INSTALLED = False


def _ensure_train_sigterm_hooks() -> None:
    """deploy/systemctl restart 时尽量在 epoch 边界停止，避免训练子进程被硬杀后难以恢复。"""
    global _TRAIN_SIGTERM_HOOKS_INSTALLED
    if _TRAIN_SIGTERM_HOOKS_INSTALLED:
        return
    _TRAIN_SIGTERM_HOOKS_INSTALLED = True

    def _on_sigterm(signum: int, frame: Any) -> None:  # noqa: ARG001
        ids = list(_ACTIVE_TRAIN_JOBS)
        if not ids:
            return
        logger.warning(
            "SIGTERM received; requesting graceful stop for active YOLO train jobs: {}",
            ids,
        )
        for jid in ids:
            request_stop_train_job(jid)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        logger.debug("SIGTERM hook not installed for YOLO train (non-main thread?)")


class TrainStoppedByUser(Exception):
    """用户请求暂停训练。"""


class TrainExecutionFenceLost(RuntimeError):
    """独立训练 Worker 失去 owner/token 后禁止继续写入训练结果。"""


def _assert_train_execution_fence(
    db: Session,
    *,
    job_id: int,
    execution_owner: str | None,
    execution_fencing_token: int | None,
) -> None:
    """在训练结果写入前按 owner/token/未过期条件确认当前 Worker。"""
    if execution_owner is None and execution_fencing_token is None:
        return
    if execution_owner is None or execution_fencing_token is None:
        raise TrainExecutionFenceLost("训练执行租约上下文不完整，拒绝结果写入")
    try:
        current = (
            db.query(YoloTrainJob.id)
            .filter(
                YoloTrainJob.id == int(job_id),
                YoloTrainJob.execution_lease_owner == str(execution_owner),
                YoloTrainJob.execution_fencing_token == int(execution_fencing_token),
                YoloTrainJob.execution_lease_expires_at.isnot(None),
                YoloTrainJob.execution_lease_expires_at > datetime.now(),
            )
            .first()
        )
    except Exception as exc:
        raise TrainExecutionFenceLost(
            f"训练任务 {job_id} 无法确认执行租约，拒绝结果写入"
        ) from exc
    if current is None:
        raise TrainExecutionFenceLost(
            f"训练任务 {job_id} execution fencing token 已失效"
        )


def _assert_train_result_write_fence(
    db: Session,
    *,
    job_id: int,
    execution_owner: str | None,
    execution_fencing_token: int | None,
) -> None:
    """Protect terminal/status writes for both new and legacy callers.

    Fenced workers must match owner/token. A legacy caller without execution
    context is only allowed while no active execution lease exists; otherwise
    it is an old Web/process writer and must fail closed.
    """
    if execution_owner is not None or execution_fencing_token is not None:
        _assert_train_execution_fence(
            db,
            job_id=job_id,
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
        return
    if not isinstance(db, Session):
        # Isolated historical unit tests sometimes use a mock session and do
        # not model lease columns; preserve their no-context compatibility.
        return
    try:
        current = (
            db.query(
                YoloTrainJob.execution_lease_owner,
                YoloTrainJob.execution_lease_expires_at,
            )
            .filter(YoloTrainJob.id == int(job_id))
            .first()
        )
    except Exception as exc:
        raise TrainExecutionFenceLost(
            f"训练任务 {job_id} 无法确认执行租约，拒绝结果写入"
        ) from exc
    if (
        current is not None
        and current.execution_lease_owner
        and current.execution_lease_expires_at is not None
        and current.execution_lease_expires_at > datetime.now()
    ):
        raise TrainExecutionFenceLost(
            f"训练任务 {job_id} 存在有效执行租约，拒绝无上下文结果写入"
        )


def _train_execution_write_guard(
    job_id: int,
    *,
    execution_owner: str | None,
    execution_fencing_token: int | None,
) -> Callable[[], None]:
    """Build a cheap callback fence for writes made inside Ultralytics.

    Ultralytics owns the ``results.csv`` and checkpoint writes, so the parent
    Worker cannot wrap those filesystem operations in a database transaction.
    The callback runs at batch/epoch boundaries and makes a lost lease stop the
    child before it can publish another progress boundary. Legacy callers keep
    the existing no-context behavior and are still protected by the final
    result fence.
    """

    def _guard() -> None:
        if execution_owner is None and execution_fencing_token is None:
            return
        with session_scope() as db:
            _assert_train_execution_fence(
                db,
                job_id=int(job_id),
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )

    return _guard


def _stop_flag_path(job_id: int) -> Path:
    return yolo_runtime.stop_flag_path(_job_work_root, job_id)


def _user_pause_flag_path(job_id: int) -> Path:
    """仅 API「暂停」时写入；有此标记则不自动续训。"""
    return yolo_runtime.user_pause_flag_path(_job_work_root, job_id)


def _pending_runtime_hyperparams_path(job_id: int) -> Path:
    return yolo_runtime.pending_runtime_hyperparams_path(_job_work_root, job_id)


def mark_user_pause_requested(job_id: int) -> None:
    yolo_runtime.mark_user_pause_requested(
        _job_work_root,
        _user_pause_flag_path,
        logger,
        job_id,
    )


def is_user_pause_requested(job_id: int) -> bool:
    return yolo_runtime.is_user_pause_requested(_user_pause_flag_path, job_id)


def clear_user_pause_requested(job_id: int) -> None:
    yolo_runtime.clear_user_pause_requested(_user_pause_flag_path, logger, job_id)


def is_stop_requested(job_id: int) -> bool:
    return yolo_runtime.is_stop_requested(_stop_flag_path, job_id)


def request_stop_train_job(job_id: int) -> None:
    yolo_runtime.request_stop_train_job(
        _job_work_root,
        _stop_flag_path,
        logger,
        job_id,
    )


def clear_stop_train_job(job_id: int) -> None:
    yolo_runtime.clear_stop_train_job(_stop_flag_path, logger, job_id)


def read_pending_runtime_hyperparams(job_id: int) -> Optional[dict[str, Any]]:
    return yolo_runtime.read_pending_runtime_hyperparams(
        _pending_runtime_hyperparams_path,
        job_id,
    )


def _write_pending_runtime_hyperparams(
    job_id: int, hyperparams: dict[str, Any]
) -> None:
    yolo_runtime.write_pending_runtime_hyperparams(
        _job_work_root,
        _pending_runtime_hyperparams_path,
        logger,
        job_id,
        hyperparams,
    )


def _clear_pending_runtime_hyperparams(job_id: int) -> None:
    yolo_runtime.clear_pending_runtime_hyperparams(
        _pending_runtime_hyperparams_path,
        logger,
        job_id,
    )


def _has_pending_runtime_hyperparams(job_id: int) -> bool:
    return yolo_runtime.has_pending_runtime_hyperparams(
        _pending_runtime_hyperparams_path,
        job_id,
    )


def _append_train_process_log(job_id: int, line: str) -> None:
    yolo_runtime.append_train_process_log(_job_work_root, job_id, line)


def _disable_ultralytics_online_amp_check(
    train_log: Any = None,
    *,
    job_id: Optional[int] = None,
) -> None:
    def _write_log(line: str) -> None:
        if train_log is not None:
            train_log.write(line)
        elif job_id is not None:
            _append_train_process_log(int(job_id), line)
        else:
            logger.info(line)

    try:
        from ultralytics.engine import trainer as ultralytics_trainer
    except Exception as exc:
        _write_log(f"skip_online_amp_check unavailable reason={exc}")
        return

    def _offline_check_amp(*args: Any, **kwargs: Any) -> bool:
        return True

    ultralytics_trainer.check_amp = _offline_check_amp
    _write_log(
        "skip_online_amp_check patched ultralytics.engine.trainer.check_amp -> True"
    )


def _log_weight_files_to_process_log(
    writer: Any, weights_dir: Path, *, prefix: str = "weights"
) -> None:
    yolo_runtime.log_weight_files_to_process_log(
        writer,
        weights_dir,
        prefix=prefix,
    )


def _make_train_process_callbacks(
    job_id: int,
    train_log: Any,
    *,
    work_root: Path,
    target_epochs: int,
    execution_guard: Callable[[], None] | None = None,
):
    """Ultralytics 回调：暂停检测 + 每 epoch / 权重写入 process.log。"""
    from labelu.internal.common.yolo_training_metrics import (
        format_results_csv_epoch_log_line,
    )

    return yolo_runtime.make_train_process_callbacks(
        job_id,
        train_log,
        work_root=work_root,
        target_epochs=target_epochs,
        format_results_csv_epoch_log_line=format_results_csv_epoch_log_line,
        has_pending_runtime_hyperparams_fn=_has_pending_runtime_hyperparams,
        request_stop_train_job_fn=request_stop_train_job,
        is_stop_requested_fn=is_stop_requested,
        log_weight_files_fn=_log_weight_files_to_process_log,
        execution_guard=execution_guard,
    )


def _job_is_cancelled(job: Optional[YoloTrainJob]) -> bool:
    return bool(job and job.status == YoloTrainStatus.CANCELLED.value)


def _raise_if_stop_requested(job_id: int) -> None:
    if is_stop_requested(job_id):
        if _has_pending_runtime_hyperparams(job_id):
            raise TrainStoppedByUser("应用临时训练超参数")
        raise TrainStoppedByUser("用户暂停训练")
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if _job_is_cancelled(job):
            raise TrainStoppedByUser("用户暂停训练")


def _is_spawn_train_child() -> bool:
    """spawn 子进程内运行训练时，避免在 CUDA 析构阶段跑 post-train 副作用。"""
    try:
        return mp.current_process().name != "MainProcess"
    except Exception:
        return False


def _job_train_completed_successfully(job_id: int) -> bool:
    phase, _, _, _ = _read_train_phase(job_id)
    if phase == "completed":
        return True
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
        if not job:
            return False
        return str(job.status or "") == YoloTrainStatus.COMPLETED.value


def _run_post_train_side_effects(job_id: int) -> None:
    """训练 COMPLETED 后的模型登记、编译、AI 评估与标注对比。"""
    marker = _job_work_root(job_id) / ".post_train_hooks_done"
    if marker.is_file():
        return
    try:
        from labelu.internal.application.service.model_version import (
            register_completed_training_job_system,
        )

        register_completed_training_job_system(job_id)
    except Exception:
        logger.exception("YOLO train job {} automatic PT registration failed", job_id)
    try:
        from labelu.internal.application.service.yolo_bm1688_auto_compile import (
            maybe_auto_compile_job,
        )

        maybe_auto_compile_job(job_id)
    except Exception:
        logger.exception(
            "YOLO train job {} automatic BM1688 compile trigger failed", job_id
        )
    try:
        from labelu.internal.application.service.yolo_train_gt_pred_compare import (
            schedule_gt_pred_compare_after_train,
        )

        schedule_gt_pred_compare_after_train(job_id)
    except Exception:
        logger.exception("YOLO train job {} gt_pred_compare trigger failed", job_id)
    try:
        from labelu.internal.application.service.yolo_train_evaluation import (
            schedule_auto_evaluations_after_train,
        )

        schedule_auto_evaluations_after_train(job_id)
    except Exception:
        logger.exception("YOLO train job {} auto eval trigger failed", job_id)
    try:
        marker.write_text("1", encoding="utf-8")
    except OSError:
        pass


_POST_TRAIN_RECOVERY_LOCK = threading.Lock()
_POST_TRAIN_RECOVERY_STARTED = False


def _has_automatic_training_lineage(job: Any) -> bool:
    return bool(
        str(getattr(job, "queue_source", "") or "").strip().upper() == "AUTO"
        or getattr(job, "auto_training_plan_id", None) is not None
        or getattr(job, "auto_training_round_id", None) is not None
    )


def _completed_post_train_recovery_jobs(db: Session) -> list[YoloTrainJob]:
    if settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        return crud_yolo_train_job.list_completed_with_weights(db)
    return (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
            YoloTrainJob.weights_path.isnot(None),
            YoloTrainJob.weights_path != "",
            func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL")) != "AUTO",
            YoloTrainJob.auto_training_plan_id.is_(None),
            YoloTrainJob.auto_training_round_id.is_(None),
        )
        .order_by(YoloTrainJob.id.asc())
        .all()
    )


def recover_completed_post_train_jobs() -> dict[str, int]:
    """Recover post-train stages whose owning backend process has exited."""
    from labelu.internal.application.service.yolo_train_evaluation import (
        schedule_missing_auto_evaluations_for_job,
    )
    from labelu.internal.application.service.yolo_train_gt_pred_compare import (
        recover_gt_pred_compare_after_restart,
    )
    from labelu.internal.application.service.model_version import (
        register_completed_training_job_system,
    )
    from labelu.internal.common.db import SessionLocal

    db = SessionLocal()
    try:
        jobs = _completed_post_train_recovery_jobs(db)
        job_states = [
            (
                int(job.id),
                str(getattr(job, "ai_eval_full_status", None) or ""),
                str(getattr(job, "ai_eval_next_status", None) or ""),
                _has_automatic_training_lineage(job),
            )
            for job in jobs
        ]
    finally:
        db.close()

    registered_models = 0
    recovered_eval = 0
    recovered_compare = 0
    for job_id, full_eval_status, next_eval_status, automatic_training in job_states:
        try:
            if register_completed_training_job_system(job_id):
                registered_models += 1
        except Exception:
            logger.exception("post-train recovery PT registration failed job {}", job_id)
        if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED and automatic_training:
            continue
        marker = _job_work_root(job_id) / ".post_train_hooks_done"
        marker_missing = not marker.is_file()
        recovery_dispatched = True
        if marker_missing or {"RUNNING", "FAILED"} & {
            full_eval_status,
            next_eval_status,
        }:
            try:
                if schedule_missing_auto_evaluations_for_job(job_id):
                    recovered_eval += 1
            except Exception:
                recovery_dispatched = False
                logger.exception("post-train recovery auto eval failed job {}", job_id)
        try:
            if recover_gt_pred_compare_after_restart(
                job_id, include_idle=marker_missing
            ):
                recovered_compare += 1
        except Exception:
            recovery_dispatched = False
            logger.exception("post-train recovery compare failed job {}", job_id)
        if marker_missing and recovery_dispatched:
            try:
                marker.write_text("1", encoding="utf-8")
            except OSError:
                logger.warning("post-train recovery marker write failed job {}", job_id)

    if registered_models or recovered_eval or recovered_compare:
        logger.info(
            "post-train recovery registered_models={} scheduled eval={} compare={}",
            registered_models,
            recovered_eval,
            recovered_compare,
        )
    return {"eval": recovered_eval, "compare": recovered_compare}


def schedule_completed_post_train_recovery(delay_seconds: float = 1.0) -> bool:
    global _POST_TRAIN_RECOVERY_STARTED
    with _POST_TRAIN_RECOVERY_LOCK:
        if _POST_TRAIN_RECOVERY_STARTED:
            return False
        _POST_TRAIN_RECOVERY_STARTED = True

    def _run() -> None:
        if delay_seconds > 0:
            time.sleep(float(delay_seconds))
        try:
            recover_completed_post_train_jobs()
        except Exception:
            logger.exception("post-train recovery scan failed")

    threading.Thread(
        target=_run,
        name="yolo-post-train-recovery",
        daemon=True,
    ).start()
    return True


def _handle_spawn_child_exit(
    job_id: int,
    exit_code: Optional[int],
    *,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    def mark_failed(child_job_id: int, child_exit_code: int) -> None:
        _mark_spawn_child_failed(
            child_job_id,
            child_exit_code,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )

    yolo_lifecycle.handle_spawn_child_exit(
        _job_train_completed_successfully,
        _run_post_train_side_effects,
        mark_failed,
        job_id,
        exit_code,
    )


def _auto_resume_backoff_path(job_id: int) -> Path:
    return yolo_lifecycle.auto_resume_backoff_path(_job_work_root, job_id)


def _record_auto_resume_failure(job_id: int, reason: str) -> None:
    yolo_lifecycle.record_auto_resume_failure(
        _auto_resume_backoff_path,
        logger,
        job_id,
        reason,
    )


def _clear_auto_resume_backoff(job_id: int) -> None:
    yolo_lifecycle.clear_auto_resume_backoff(_auto_resume_backoff_path, job_id)


def _auto_resume_backoff_active(job_id: int, *, max_failures: int = 5) -> bool:
    return yolo_lifecycle.auto_resume_backoff_active(
        _auto_resume_backoff_path,
        job_id,
        max_failures=max_failures,
    )


def _mark_spawn_child_failed(
    job_id: int,
    exit_code: int | str,
    *,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    """spawn 训练子进程非正常退出时写入失败状态（如部署重启 SIGKILL、DDP 崩溃）。"""
    if execution_owner is not None or execution_fencing_token is not None:
        try:
            with session_scope() as fence_db:
                _assert_train_result_write_fence(
                    fence_db,
                    job_id=int(job_id),
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
        except TrainExecutionFenceLost as exc:
            logger.warning(
                "ignore stale spawn child failure job_id={} exit_code={}: {}",
                job_id,
                exit_code,
                exc,
            )
            _append_train_process_log(
                job_id,
                f"spawn_child_exit_ignored_stale exit_code={exit_code}",
            )
            return

    if _job_train_completed_successfully(job_id):
        logger.warning(
            "YOLO train job {} spawn child exit_code={} after COMPLETED; keep success status",
            job_id,
            exit_code,
        )
        _append_train_process_log(
            job_id,
            f"spawn_child_exit_ignored exit_code={exit_code} (job already completed)",
        )
        _run_post_train_side_effects(job_id)
        return
    detail = f"训练子进程异常退出 exit_code={exit_code}"
    if isinstance(exit_code, str):
        detail = exit_code[:500]
    _append_train_process_log(job_id, f"phase=failed detail={detail}")
    _write_train_phase(
        job_id,
        "failed",
        detail=detail[:500],
        execution_owner=execution_owner,
        execution_fencing_token=execution_fencing_token,
    )
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job or _job_is_cancelled(job):
            return
        if str(job.status or "") in (
            YoloTrainStatus.COMPLETED.value,
            YoloTrainStatus.FAILED.value,
            YoloTrainStatus.CANCELLED.value,
        ):
            return
        with begin_transaction(db):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job or _job_is_cancelled(job):
                return
            _assert_train_result_write_fence(
                db,
                job_id=job_id,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            crud_yolo_train_job.update_status(
                db,
                job,
                YoloTrainStatus.FAILED.value,
                error_message=detail[:500],
            )


def _mark_job_cancelled(
    job_id: int,
    *,
    detail: str = "用户暂停训练",
    db: Optional[Session] = None,
    skip_auto_resume: bool = False,
    runtime_pid: Optional[int] = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    def _apply(active_db: Session) -> None:
        job = crud_yolo_train_job.get(db=active_db, job_id=job_id)
        if not job or _job_is_cancelled(job):
            return
        with begin_transaction(active_db):
            job = crud_yolo_train_job.get(db=active_db, job_id=job_id)
            if not job or _job_is_cancelled(job):
                return
            _assert_train_result_write_fence(
                active_db,
                job_id=job_id,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            crud_yolo_train_job.update_status(
                active_db,
                job,
                YoloTrainStatus.CANCELLED.value,
                error_message=detail[:500],
            )

    if db is not None:
        _apply(db)
    else:
        from labelu.internal.common.db import session_scope

        with session_scope() as scoped:
            _apply(scoped)
    _write_train_phase(
        job_id,
        "cancelled",
        detail=detail,
        runtime_pid=runtime_pid,
        execution_owner=execution_owner,
        execution_fencing_token=execution_fencing_token,
    )
    if not skip_auto_resume:
        _schedule_auto_resume_if_applicable(job_id)


def _schedule_auto_resume_if_applicable(job_id: int) -> None:
    if not settings.YOLO_AUTO_RESUME_TRAIN_ENABLED:
        return
    try:
        from labelu.internal.common.yolo_train_deploy import is_deploy_pause_in_progress

        if is_deploy_pause_in_progress():
            return
    except Exception:
        pass
    if is_user_pause_requested(job_id):
        return

    def _run() -> None:
        try:
            from labelu.internal.application.service.yolo_train_auto_resume import (
                maybe_auto_resume_train_job,
            )

            maybe_auto_resume_train_job(job_id)
        except Exception:
            logger.exception("auto-resume after cancel failed job_id={}", job_id)

    threading.Thread(target=_run, daemon=True).start()


def job_can_auto_resume(job: YoloTrainJob) -> bool:
    """是否满足自动续训条件（不含人为暂停标记）。"""
    if not settings.YOLO_TRAIN_ENABLED or not settings.YOLO_AUTO_RESUME_TRAIN_ENABLED:
        return False
    if _auto_resume_backoff_active(int(job.id)):
        return False
    if int(job.id) in _ACTIVE_TRAIN_JOBS:
        return False
    if execution_lease_is_active(job):
        return False
    runtime = _runtime_fields_for_job(job)
    if not runtime.get("is_restartable"):
        return False
    work_root = _job_work_root(int(job.id))
    last_path = work_root / "train" / "weights" / "last.pt"
    plate_resume_state = False
    if _is_plate_job(job):
        plate_root = work_root / (
            "plate_detection" if _is_plate_detection_job(job) else "plate_components"
        )
        plate_resume_state = plate_root.is_dir() and (
            any(plate_root.rglob("last.pt"))
            or any((plate_root / "progress").glob("*.json"))
        )
    if not last_path.is_file() and not plate_resume_state:
        return False
    status = str(job.status or "")
    if status == YoloTrainStatus.CANCELLED.value:
        return True
    if status in (
        YoloTrainStatus.PROCESSING.value,
        YoloTrainStatus.PENDING.value,
    ):
        from labelu.internal.application.service.yolo_train_queue import (
            active_yolo_train_job_ids_on_gpu,
            yolo_train_resources_available,
        )

        if int(job.id) in active_yolo_train_job_ids_on_gpu():
            return False
        available, _reason = yolo_train_resources_available(exclude_job_id=int(job.id))
        if not available:
            return False
        return runtime.get("runtime_status") == "STOPPED"
    return False


def _resume_train_job_from_checkpoint(db: Session, job_id: int) -> bool:
    from labelu.internal.application.service.yolo_train_queue import command_from_job

    return yolo_resume.resume_train_job_from_checkpoint(
        ensure_ultralytics_installed,
        crud_yolo_train_job,
        job_can_auto_resume,
        is_user_pause_requested,
        _ACTIVE_TRAIN_JOBS,
        _job_work_root,
        command_from_job,
        clear_stop_train_job,
        _clear_auto_resume_backoff,
        begin_transaction,
        crud_yolo_train_job.update_status,
        _submit_yolo_train_job,
        YoloTrainStatus.PROCESSING.value,
        execution_lease_active_fn=execution_lease_is_active,
        db=db,
        job_id=job_id,
        ensure_reason="resume existing yolo train job",
    )


def _job_can_manual_restart(job: YoloTrainJob) -> bool:
    runtime = _runtime_fields_for_job(job)
    if runtime.get("runtime_status") == "RUNNING":
        return False
    if runtime.get("is_restartable"):
        return True
    if str(job.status or "") in (
        YoloTrainStatus.CANCELLED.value,
        YoloTrainStatus.FAILED.value,
    ):
        return True
    return False


def _restart_train_job_from_existing_job(db: Session, job_id: int) -> bool:
    return yolo_resume.restart_train_job_from_existing_job(
        ensure_ultralytics_installed,
        crud_yolo_train_job,
        _job_can_manual_restart,
        _launch_train_job_from_existing_config,
        db=db,
        job_id=job_id,
        ensure_reason="resume existing yolo train job",
    )


def _launch_train_job_from_existing_config(db: Session, job_id: int) -> bool:
    from labelu.internal.application.service.yolo_train_queue import command_from_job

    return yolo_resume.launch_train_job_from_existing_config(
        crud_yolo_train_job,
        _ACTIVE_TRAIN_JOBS,
        command_from_job,
        clear_stop_train_job,
        clear_user_pause_requested,
        _clear_auto_resume_backoff,
        begin_transaction,
        crud_yolo_train_job.update_status,
        _job_work_root,
        _append_train_process_log,
        _submit_yolo_train_job,
        YoloTrainStatus.PROCESSING.value,
        execution_lease_active_fn=execution_lease_is_active,
        db=db,
        job_id=job_id,
    )


def _write_train_phase(
    job_id: int,
    phase: str,
    *,
    detail: str = "",
    runtime_pid: Optional[int] = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> bool:
    from labelu.internal.common.pipeline_process_log import (
        append_log_line,
        yolo_train_log_path,
    )

    # 阶段文件是作业目录下的共享状态，旧训练进程可能在新进程接管后
    # 才迟到写入。带租约的执行器必须在每次写入前重新确认 owner/token；
    # 未带上下文的历史路径只有在当前作业没有有效执行租约时才允许写入。
    fenced = execution_owner is not None or execution_fencing_token is not None
    try:
        with session_scope() as db:
            if fenced:
                _assert_train_execution_fence(
                    db,
                    job_id=int(job_id),
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
            else:
                current = (
                    db.query(
                        YoloTrainJob.execution_lease_owner,
                        YoloTrainJob.execution_fencing_token,
                        YoloTrainJob.execution_lease_expires_at,
                    )
                    .filter(YoloTrainJob.id == int(job_id))
                    .first()
                )
                if (
                    current is not None
                    and current.execution_lease_owner
                    and current.execution_lease_expires_at is not None
                    and current.execution_lease_expires_at > datetime.now()
                ):
                    logger.warning(
                        "reject unfenced stale phase write job_id={} phase={} active_owner={} active_token={}",
                        job_id,
                        phase,
                        current.execution_lease_owner,
                        current.execution_fencing_token,
                    )
                    return False
    except TrainExecutionFenceLost as exc:
        logger.warning(
            "reject fenced stale phase write job_id={} phase={}: {}",
            job_id,
            phase,
            exc,
        )
        return False
    except Exception as exc:
        # 有租约时必须 fail-closed，避免数据库不可用期间旧执行器覆盖阶段；
        # 无租约的历史本地路径保留原有文件进度兼容性。
        if fenced:
            logger.warning(
                "phase fence check failed; reject write job_id={} phase={}: {}",
                job_id,
                phase,
                exc,
            )
            return False
        logger.debug("phase fence check unavailable job_id={}: {}", job_id, exc)

    root = _job_work_root(job_id)
    root.mkdir(parents=True, exist_ok=True)
    log_path = yolo_train_log_path(job_id)
    payload = {
        "phase": phase,
        "detail": detail,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "runtime_pid": int(runtime_pid) if runtime_pid is not None else os.getpid(),
        "process_log_path": str(log_path),
    }
    if fenced:
        payload["execution_owner"] = str(execution_owner)
        payload["execution_fencing_token"] = int(execution_fencing_token)
        payload["phase_generation"] = (
            f"{execution_owner}:{int(execution_fencing_token)}"
        )
    try:
        phase_path = root / "phase.json"
        tmp_path = root / f".phase.{os.getpid()}.tmp"
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, phase_path)
    except OSError as exc:
        logger.warning("write phase.json failed job_id={}: {}", job_id, exc)
        return False
    line = f"phase={phase}" + (f" detail={detail}" if detail else "")
    append_log_line(log_path, line, logger_prefix=f"YOLO train job_id={job_id}")
    logger.info("YOLO train job {} phase={} {}", job_id, phase, detail or "")
    return True


def _nvidia_compute_pids() -> set[int]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("nvidia-smi pid query failed: {}", exc)
        return set()
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        if msg:
            logger.warning(
                "nvidia-smi pid query returned {}: {}", proc.returncode, msg[:300]
            )
        return set()
    out: set[int] = set()
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.add(int(line))
        except ValueError:
            continue
    return out


def _linux_process_child_pids(pid: int, proc_root: Path = Path("/proc")) -> set[int]:
    children: set[int] = set()
    child_files: list[Path] = []
    task_root = proc_root / str(pid) / "task"
    try:
        task_dirs = [p for p in task_root.iterdir() if p.is_dir()]
    except OSError:
        task_dirs = []
    for task_dir in task_dirs:
        child_files.append(task_dir / "children")
    child_files.append(proc_root / str(pid) / "children")

    for children_path in child_files:
        try:
            raw = children_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        for part in raw.split():
            try:
                child = int(part)
            except ValueError:
                continue
            if child > 0:
                children.add(child)
    return children


def _linux_process_tree_pids(
    root_pid: int, proc_root: Path = Path("/proc")
) -> set[int]:
    """收集 root_pid 及其子进程（Linux /proc）；用于双卡训练时 GPU 在子进程上的情况。"""
    if root_pid <= 0:
        return set()
    out: set[int] = {root_pid}
    if not proc_root.is_dir():
        return out
    queue = [root_pid]
    seen: set[int] = set()
    while queue:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.add(pid)
        queue.extend(_linux_process_child_pids(pid, proc_root=proc_root) - seen)
    return out


def _runtime_train_process_on_gpu(runtime_pid: int) -> bool:
    """
    判断训练相关进程是否在 GPU 上。
    device=0,1 时 Ultralytics 常在子进程占卡，主进程 PID（phase.json 中的 runtime_pid）不会出现在 nvidia-smi 中。
    """
    if runtime_pid <= 0:
        return False
    nvidia_pids = _nvidia_compute_pids()
    if not nvidia_pids:
        return False
    if runtime_pid in nvidia_pids:
        return True
    return bool(_linux_process_tree_pids(runtime_pid) & nvidia_pids)


def _terminate_train_process_tree(
    job_id: int,
    runtime_pid: Optional[int],
    *,
    include_root: bool = True,
) -> None:
    if not runtime_pid or runtime_pid <= 0:
        return
    if int(runtime_pid) == os.getpid():
        include_root = False
    pids = sorted(_linux_process_tree_pids(int(runtime_pid)), reverse=True)
    if not include_root:
        pids = [pid for pid in pids if pid != int(runtime_pid)]
    if not pids:
        return
    terminated: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            logger.warning("pause job {} cannot terminate pid {}: {}", job_id, pid, exc)
        except OSError as exc:
            logger.warning("pause job {} terminate pid {} failed: {}", job_id, pid, exc)
    if terminated:
        _append_train_process_log(
            job_id,
            "pause=terminate_process_tree pids=" + ",".join(str(p) for p in terminated),
        )


def _cleanup_train_runtime_after_terminal_failure(job_id: int) -> None:
    """失败/取消终态清理：pending 调参不应继续挂在页面，残留运行 pid 要尽量终止。"""
    yolo_lifecycle.cleanup_train_runtime_after_terminal_failure(
        _read_train_phase,
        _terminate_train_process_tree,
        _clear_pending_runtime_hyperparams,
        clear_stop_train_job,
        clear_user_pause_requested,
        job_id,
    )


def prepare_cuda_env_for_yolo_train() -> None:
    """在首次 import torch 之前调用：空 CUDA_VISIBLE_DEVICES 会导致 is_available()=False。"""
    key = "CUDA_VISIBLE_DEVICES"
    if os.environ.get(key) == "":
        os.environ.pop(key, None)
        logger.warning(
            "已移除空的 CUDA_VISIBLE_DEVICES（该环境变量为空时 PyTorch 会判定无可用 GPU）"
        )


def _cuda_runtime_snapshot() -> Tuple[bool, int]:
    prepare_cuda_env_for_yolo_train()
    try:
        import torch

        return bool(torch.cuda.is_available()), int(torch.cuda.device_count())
    except Exception:
        return False, 0


def _is_cpu_train_device(device: str) -> bool:
    return (device or "").strip().lower() in ("", "cpu")


def _requested_gpu_indices(device: str) -> List[int]:
    token = (device or "").strip().lower()
    if _is_cpu_train_device(token):
        return []
    normalized = token.replace("cuda:", "").replace("cuda", "").strip()
    normalized = normalized.replace(",", " ")
    indices: List[int] = []
    for part in normalized.split():
        if not part:
            continue
        indices.append(int(part))
    return indices


def _fallback_train_device_for_visible_gpus(device_count: int) -> str:
    if device_count <= 0:
        return "cpu"
    if device_count == 1:
        return "0"
    return ",".join(str(i) for i in range(device_count))


def _sanitize_explicit_yolo_train_device(explicit: str) -> str:
    """校验 .env 中 YOLO_TRAIN_DEVICE，避免 CUDA 不可用时仍传入 0,1 导致 Ultralytics 报错。"""
    raw = (explicit or "").strip()
    if not raw:
        return raw
    if _is_cpu_train_device(raw):
        return "cpu"

    cuda_ok, device_count = _cuda_runtime_snapshot()
    if not cuda_ok:
        logger.warning(
            "YOLO_TRAIN_DEVICE={} 但 torch.cuda.is_available()=False，回退 cpu。{} "
            "CUDA_VISIBLE_DEVICES={!r}",
            raw,
            _torch_cuda_summary(),
            os.environ.get("CUDA_VISIBLE_DEVICES"),
        )
        return "cpu"

    try:
        indices = _requested_gpu_indices(raw)
    except ValueError:
        logger.warning("YOLO_TRAIN_DEVICE={} 无法解析 GPU 编号，回退 cpu", raw)
        return "cpu"

    if not indices:
        return _fallback_train_device_for_visible_gpus(device_count)

    if max(indices) >= device_count:
        fallback = _fallback_train_device_for_visible_gpus(device_count)
        logger.warning(
            "YOLO_TRAIN_DEVICE={} 超出当前可见 GPU 数量 {}，回退 {}",
            raw,
            device_count,
            fallback,
        )
        return fallback

    if "," in raw:
        return ",".join(str(i) for i in indices)
    if " " in raw.strip():
        return " ".join(str(i) for i in indices)
    return raw


def yolo_train_python_executable() -> str:
    return sys.executable or "unknown"


def _torch_cuda_summary() -> str:
    try:
        import torch

        cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        py = yolo_train_python_executable()
        if not torch.cuda.is_available():
            return (
                f"python={py} PyTorch {torch.__version__} cuda_available=False "
                f"device_count={torch.cuda.device_count()} "
                f"CUDA_VISIBLE_DEVICES={cuda_visible!r} "
                "(训练将使用 CPU；请在 labelu venv 安装 CUDA 版 torch，勿仅用 conda base 验证)"
            )
        names = [
            torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
        ]
        return (
            f"python={py} PyTorch {torch.__version__} cuda_available=True "
            f"device_count={torch.cuda.device_count()} "
            f"CUDA_VISIBLE_DEVICES={cuda_visible!r} names={names}"
        )
    except Exception as exc:
        return f"python={yolo_train_python_executable()} PyTorch/CUDA 检查失败: {exc}"


def yolo_train_cuda_mismatch_hint() -> Optional[str]:
    """YOLO_TRAIN_DEVICE 要求 GPU 但当前解释器 CUDA 不可用时返回运维提示。"""
    explicit = (settings.YOLO_TRAIN_DEVICE or "").strip()
    if not explicit or _is_cpu_train_device(explicit):
        return None
    cuda_ok, _ = _cuda_runtime_snapshot()
    if cuda_ok:
        return None
    py = Path(yolo_train_python_executable())
    pip = py.parent / "pip"
    return (
        f"YOLO_TRAIN_DEVICE={explicit} 但 {py} 中 torch.cuda.is_available()=False。"
        " systemd 使用项目 venv，与 conda (base) 不是同一 Python。"
        " 修复: VENV 通常在 /data/labeIU/labeIuMedia/venv（非代码目录下 ./venv）"
        "；sudo bash scripts/ops/ensure_yolo_train_cuda_torch.sh"
        f" 或 {pip} install --upgrade torch torchvision"
        " --index-url https://download.pytorch.org/whl/cu124 后 systemctl restart labelu"
    )


_MIN_PRETRAINED_WEIGHT_BYTES = (
    100 * 1024
)  # 过小的 .pt（如 wget 失败留下的 0 字节）视为无效


def _weights_basename(stored: str | None) -> Optional[str]:
    if not stored or not str(stored).strip():
        return None
    return Path(str(stored).strip()).name or None


PLATFORM_PRESET_WEIGHT = "yolo11n.pt"


def _normalize_preset_model_weights(model_weights: str) -> str:
    """平台训练仅支持官方预训练 yolo11n.pt（BM1688 导出与之对齐 yolov11n）。"""
    name = (model_weights or PLATFORM_PRESET_WEIGHT).strip() or PLATFORM_PRESET_WEIGHT
    base = Path(name).name.lower()
    if base != PLATFORM_PRESET_WEIGHT:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"仅支持 {PLATFORM_PRESET_WEIGHT} 作为官方预训练权重，当前为 {name}",
        )
    return PLATFORM_PRESET_WEIGHT


def _resolve_pretrained_weights_path(model_weights: str) -> str:
    """解析 yolo11n.pt 等为绝对路径，避免 Ultralytics 在离线环境反复联网下载卡住。"""
    name = (model_weights or "yolo11n.pt").strip() or "yolo11n.pt"
    candidates: list[Path] = []
    p = Path(name)
    if p.is_absolute():
        candidates.append(p)
    else:
        extra = (settings.YOLO_PRETRAINED_WEIGHTS_DIR or "").strip()
        if extra:
            candidates.append(Path(extra) / name)
        candidates.append(Path(settings.MEDIA_ROOT) / "pretrained" / name)
        candidates.append(Path.home() / ".config" / "Ultralytics" / "weights" / name)
    for c in candidates:
        try:
            if not c.is_file():
                continue
            size = c.stat().st_size
            if size < _MIN_PRETRAINED_WEIGHT_BYTES:
                logger.warning(
                    "跳过无效预训练权重（{} bytes）: {}",
                    size,
                    c.resolve(),
                )
                continue
            resolved = str(c.resolve())
            logger.info("使用预训练权重: {} ({} bytes)", resolved, size)
            return resolved
        except OSError as exc:
            logger.warning("检查预训练权重失败 {}: {}", c, exc)
    logger.warning(
        "未在本地找到有效预训练权重 {}，将交给 Ultralytics 按名称解析（可能联网）",
        name,
    )
    return name


def _model_weights_field_for_storage(cmd: CreateYoloTrainJobCommand) -> str:
    """写入作业表的 model_weights 展示字段（含 imported:/trained: 前缀）。"""
    src = (cmd.weights_source or "preset").strip()
    if src == "imported":
        if not cmd.weights_ref_id:
            raise LabelUException(
                code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="请选择外部导入的模型",
            )
        return f"imported:{int(cmd.weights_ref_id)}"
    if src == "trained":
        if not cmd.weights_ref_id:
            raise LabelUException(
                code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="请选择本系统已训练完成的模型",
            )
        return f"trained:{int(cmd.weights_ref_id)}"
    return _normalize_preset_model_weights(cmd.model_weights)


def _resolve_imported_weights_row(
    db: Session,
    owner_user_id: int,
    model_id: int,
) -> str:
    user = crud_user.get(db, owner_user_id)
    unrestricted = bool(user and is_task_superuser(user))
    if unrestricted:
        row = crud_yolo_imported_model.get_by_id(db, model_id)
    else:
        row = crud_yolo_imported_model.get_available_for_user(
            db,
            model_id=model_id,
            user_id=owner_user_id,
            public_owner_ids=crud_yolo_imported_model.public_owner_ids(db),
        )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    path = resolve_weights_path(row.weights_rel_path)
    return str(path.resolve())


def _resolve_trained_job_weights(
    db: Session,
    owner_user_id: int,
    job_id: int,
) -> str:
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.status != YoloTrainStatus.COMPLETED.value:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="训练作业不存在或未完成，无法作为初始权重",
        )
    if not (job.weights_path or "").strip():
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="该训练作业尚无 best.pt 权重文件",
        )
    user = crud_user.get(db, owner_user_id)
    if not user:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    unrestricted = is_task_superuser(user)
    if not unrestricted and job.created_by != owner_user_id:
        src_task = crud_task.get(db=db, task_id=job.task_id)
        if not src_task:
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        assert_user_has_task_access(src_task, user)
    path = resolve_weights_path(job.weights_path)
    return str(path.resolve())


def resolve_initial_weights_for_training(
    db: Session,
    owner_user_id: int,
    model_weights: str,
) -> str:
    """将存储的 model_weights 字段解析为 Ultralytics 可用的绝对路径。"""
    token = (model_weights or "yolo11n.pt").strip() or "yolo11n.pt"
    if token.startswith("imported:"):
        try:
            model_id = int(token.split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise LabelUException(
                code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="无效的导入模型引用",
            ) from exc
        return _resolve_imported_weights_row(db, owner_user_id, model_id)
    if token.startswith("trained:"):
        try:
            job_id = int(token.split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise LabelUException(
                code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="无效的训练作业引用",
            ) from exc
        return _resolve_trained_job_weights(db, owner_user_id, job_id)
    return _resolve_pretrained_weights_path(token)


def _validate_weights_source_at_create(
    db: Session,
    current_user: User,
    cmd: CreateYoloTrainJobCommand,
) -> None:
    """创建前校验权重来源可解析，避免提交后异步训练才失败。"""
    stored = _model_weights_field_for_storage(cmd)
    resolve_initial_weights_for_training(db, current_user.id, stored)


def _resolve_yolo_train_device() -> str:
    """Ultralytics train(device=...)；未配置时优先用 GPU 0，且始终校验 CUDA 可用性。"""
    explicit = (settings.YOLO_TRAIN_DEVICE or "").strip()
    if explicit and explicit.lower() not in ("auto",):
        return _sanitize_explicit_yolo_train_device(explicit)
    cuda_ok, device_count = _cuda_runtime_snapshot()
    if cuda_ok and device_count > 0:
        return "0"
    return "cpu"


def _resolve_train_device_for_job(
    job_id: int, *, override: Optional[str] = None
) -> str:
    """作业级 device：自动分配结果 > 持久化文件 > 全局 .env。"""
    if override and str(override).strip():
        token = str(override).strip()
        if _is_cpu_train_device(token):
            return token
        return _sanitize_explicit_yolo_train_device(token)
    from labelu.internal.common.yolo_train_gpu_allocator import (
        load_persisted_gpu_allocation,
        yolo_train_gpu_auto_alloc_enabled,
    )

    if yolo_train_gpu_auto_alloc_enabled():
        persisted = load_persisted_gpu_allocation(int(job_id))
        if persisted and persisted.device:
            return persisted.device
    return _resolve_yolo_train_device()


def _is_multi_gpu_device(device: str) -> bool:
    token = (device or "").strip().lower()
    if not token or token == "cpu":
        return False
    if "," in token:
        return True
    return len(token.split()) > 1


def _dynamic_single_gpu_train_workers(*, imgsz: int, batch: int) -> int:
    del imgsz, batch
    configured = max(0, int(settings.YOLO_TRAIN_WORKERS))
    return min(configured, _MAX_SINGLE_GPU_TRAIN_WORKERS)


def _effective_single_gpu_train_workers(
    requested: Any,
    *,
    imgsz: int,
    batch: int,
) -> int:
    """Use the job value when supplied; fall back to the server default."""
    if requested is None:
        return _dynamic_single_gpu_train_workers(imgsz=imgsz, batch=batch)
    try:
        configured = max(0, int(requested))
    except (TypeError, ValueError):
        return _dynamic_single_gpu_train_workers(imgsz=imgsz, batch=batch)
    return min(configured, _MAX_SINGLE_GPU_TRAIN_WORKERS)


def _default_train_cache_mode(*, imgsz: int, train_device: str) -> bool | str:
    del imgsz, train_device
    return False


def _default_train_runtime_kwargs(
    *,
    train_device: str,
    imgsz: int,
    batch: int = 16,
) -> dict[str, Any]:
    """Stable defaults that scale better on multi-job GPU servers."""
    if _is_multi_gpu_device(train_device):
        return {
            "workers": _MULTI_GPU_TRAIN_WORKERS,
            "amp": _MULTI_GPU_TRAIN_AMP,
            "cache": False,
        }
    return {
        "workers": _dynamic_single_gpu_train_workers(imgsz=imgsz, batch=batch),
        "amp": bool(settings.YOLO_TRAIN_AMP),
        "cache": _default_train_cache_mode(imgsz=imgsz, train_device=train_device),
    }


def _should_spawn_train_process(
    train_device: Optional[str] = None,
    *,
    imgsz: int = 640,
) -> bool:
    """多卡 DDP 必须在独立子进程主线程中运行；大图(>=1024)强制单卡进程内训练更稳。"""
    if int(imgsz or 640) >= 1024:
        return True
    device = train_device if train_device is not None else _resolve_yolo_train_device()
    return _is_multi_gpu_device(device)


def _train_start_progress_signature(job_id: int) -> tuple[Any, ...]:
    work_root = _job_work_root(job_id)
    phase_path = work_root / "phase.json"
    results_path = work_root / "train" / "results.csv"
    log_path = work_root / "process.log"
    phase_mtime = 0
    results_size = 0
    results_mtime = 0
    log_tail = ""
    try:
        phase_mtime = phase_path.stat().st_mtime_ns
    except OSError:
        pass
    try:
        stat = results_path.stat()
        results_size = stat.st_size
        results_mtime = stat.st_mtime_ns
    except OSError:
        pass
    try:
        stat = log_path.stat()
        log_size = stat.st_size
        with log_path.open("rb") as f:
            f.seek(max(0, log_size - 8192))
            log_tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        pass
    started = any(
        needle in log_tail.lower()
        for needle in (
            "ultralytics_train_start",
            "train_epoch_start",
            "epoch=",
            "on_train_epoch_start",
        )
    )
    return (phase_mtime, results_size, results_mtime, started)


def _train_has_started_or_progressed(job_id: int) -> bool:
    work_root = _job_work_root(job_id)
    if (work_root / "train" / "results.csv").is_file():
        return True
    return bool(_train_start_progress_signature(job_id)[-1])


def _run_spawned_yolo_train_with_watchdog(
    proc: mp.Process,
    job_id: int,
    *,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> Optional[int]:
    timeout = max(0, int(settings.YOLO_TRAIN_START_STALL_TIMEOUT_SECONDS or 0))
    watchdog_enabled = bool(settings.YOLO_TRAIN_STALL_WATCHDOG_ENABLED) and timeout > 0
    interval = 5.0
    if not watchdog_enabled:
        proc.join()
        return proc.exitcode

    last_progress = time.monotonic()
    last_signature = _train_start_progress_signature(job_id)
    while proc.is_alive():
        proc.join(interval)
        phase, _, _runtime_pid, _detail = _read_train_phase(job_id)
        if phase in ("completed", "failed", "cancelled", "restarting"):
            break
        if phase != "running":
            last_signature = _train_start_progress_signature(job_id)
            last_progress = time.monotonic()
            continue
        signature = _train_start_progress_signature(job_id)
        if signature != last_signature or _train_has_started_or_progressed(job_id):
            last_signature = signature
            last_progress = time.monotonic()
        elapsed = time.monotonic() - last_progress
        if elapsed <= timeout:
            continue
        detail = f"训练启动超过 {timeout} 秒未产生 epoch/results 进展，已终止子进程"
        _append_train_process_log(job_id, f"watchdog=start_stall detail={detail}")
        runtime_pid = int(getattr(proc, "pid", 0) or 0)
        _terminate_train_process_tree(job_id, runtime_pid, include_root=True)
        try:
            proc.terminate()
        except Exception as exc:
            logger.warning(
                "YOLO train job {} watchdog terminate failed: {}", job_id, exc
            )
        proc.join(10)
        if proc.is_alive():
            try:
                proc.kill()
            except Exception as exc:
                logger.warning(
                    "YOLO train job {} watchdog kill failed: {}", job_id, exc
                )
            proc.join(5)
        _mark_spawn_child_failed(
            job_id,
            detail,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
        _cleanup_train_runtime_after_terminal_failure(job_id)
        return proc.exitcode if proc.exitcode is not None else -15
    proc.join()
    return proc.exitcode


def _run_yolo_train_worker(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool = False,
    train_device: Optional[str] = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    """训练任务入口：多卡走 spawn 子进程，单卡仍在当前 worker 线程执行。"""
    resolved_device = _resolve_train_device_for_job(job_id, override=train_device)
    imgsz = int(cmd.imgsz or 640)
    if _should_spawn_train_process(resolved_device, imgsz=imgsz):
        reason = (
            "large_imgsz_requires_watchdog_process"
            if imgsz >= 1024
            else "multi_gpu_ddp_requires_main_thread"
        )
        _append_train_process_log(
            job_id,
            f"launch=spawn_subprocess device={resolved_device} reason={reason}",
        )
        ctx = mp.get_context("spawn")
        proc = ctx.Process(
            target=_run_yolo_train_sync,
            args=(job_id, task_id, cmd),
            kwargs={
                "resume": resume,
                "train_device": resolved_device,
                "execution_fencing_token": execution_fencing_token,
                "execution_owner": execution_owner,
            },
            name=f"yolo-train-{job_id}",
        )
        proc.start()
        exit_code = _run_spawned_yolo_train_with_watchdog(
            proc,
            job_id,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
        if exit_code not in (0, None):
            logger.warning(
                "YOLO train job {} spawn child exit_code={}",
                job_id,
                exit_code,
            )
            _cleanup_train_runtime_after_terminal_failure(job_id)
            phase, _, _, _ = _read_train_phase(job_id)
            if phase in ("failed", "cancelled"):
                return
        _handle_spawn_child_exit(
            job_id,
            exit_code,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
        return
    _run_yolo_train_sync(
        job_id,
        task_id,
        cmd,
        resume=resume,
        train_device=resolved_device,
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )


# 多卡 DDP 固定运行时参数（与 .env / SOP 超参无关，避免 workers=8 再次卡死）
_MULTI_GPU_TRAIN_WORKERS = 0
_MULTI_GPU_TRAIN_AMP = False
_MAX_SINGLE_GPU_TRAIN_WORKERS = 8
_MAX_LARGE_IMAGE_TRAIN_WORKERS = 4


_ULTRALYTICS_RESUME_OVERRIDE_KEYS = frozenset(
    {
        "imgsz",
        "batch",
        "device",
        "close_mosaic",
        "save_period",
        "workers",
        "cache",
        "patience",
        "time",
        "freeze",
        "val",
        "plots",
    }
)


def _apply_multi_gpu_train_runtime(
    merged: dict,
    *,
    train_device: str,
    imgsz: int,
    batch: int,
) -> dict:
    if _is_multi_gpu_device(train_device):
        merged["workers"] = _MULTI_GPU_TRAIN_WORKERS
        merged["amp"] = _MULTI_GPU_TRAIN_AMP
        merged["cache"] = False
        return merged
    runtime_defaults = _default_train_runtime_kwargs(
        train_device=train_device,
        imgsz=imgsz,
        batch=batch,
    )
    merged["workers"] = _effective_single_gpu_train_workers(
        merged.get("workers"),
        imgsz=imgsz,
        batch=batch,
    )
    merged.setdefault("amp", runtime_defaults["amp"])
    merged["cache"] = False
    return merged


def _is_retryable_train_failure(exc: BaseException) -> bool:
    msg = f"{exc} {getattr(exc, 'args', '')}".lower()
    needles = (
        "cuda out of memory",
        "outofmemoryerror",
        "out of memory",
        "cudnn_status_alloc_failed",
        "cublas",
        "launch failure",
    )
    return any(n in msg for n in needles)


def _batch_fallback_values(initial: int) -> List[int]:
    b = max(1, int(initial or 1))
    out: List[int] = [b]
    for v in (max(1, b // 2), max(1, b // 4), max(1, b // 8), 1):
        if v not in out:
            out.append(v)
    return out


def _truthy_train_flag(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def _adjust_batch_for_single_sample_tail(
    *,
    batch: int,
    train_sample_count: int,
) -> int:
    """Avoid a one-sample tail batch that can trip BatchNorm during training."""
    batch = max(1, int(batch or 1))
    train_sample_count = max(0, int(train_sample_count or 0))
    if batch <= 1 or train_sample_count <= batch:
        return batch
    if train_sample_count % batch != 1:
        return batch
    for candidate in range(batch - 1, 1, -1):
        if train_sample_count % candidate != 1:
            return candidate
    return batch


def _ensure_safe_final_train_batch(
    train_kw: dict[str, Any],
    *,
    train_sample_count: int,
    job_id: int,
    train_log: Any,
) -> None:
    current_batch = int(train_kw.get("batch") or 1)
    safe_batch = _adjust_batch_for_single_sample_tail(
        batch=current_batch,
        train_sample_count=train_sample_count,
    )
    if safe_batch == current_batch:
        return
    train_log.write(
        "train_final_batch_guard job="
        f"{job_id} adjust batch {current_batch} -> {safe_batch} "
        f"(train_samples={train_sample_count})"
    )
    train_kw["batch"] = safe_batch


def _apply_large_imgsz_safe_train_profile(
    train_kw: dict[str, Any],
    hp_extra: dict[str, Any],
    *,
    train_device: str,
    job_id: int,
    train_log: Any,
    train_sample_count: int = 0,
) -> str:
    """大图训练避免 #97 类 DDP/OOM：优先单卡并限制 batch，单卡时恢复 AI/用户 amp。"""
    device = train_device
    imgsz = int(train_kw.get("imgsz") or 640)
    batch = int(train_kw.get("batch") or 16)
    multi_scale = _truthy_train_flag(train_kw.get("multi_scale"))

    if _is_multi_gpu_device(device) and imgsz >= 1024:
        from labelu.internal.common.yolo_train_gpu_allocator import (
            choose_single_gpu_device,
            parse_device_indices,
        )

        chosen_single = choose_single_gpu_device(
            candidate_indices=parse_device_indices(device),
        )
        device = chosen_single or str(parse_device_indices(device)[0])
        train_kw["device"] = device
        train_log.write(
            f"train_safe_profile job={job_id} large_imgsz={imgsz}: use single GPU {device} (avoid DDP OOM)"
        )
        remerged = _merge_train_hyperparams(
            train_kw,
            hp_extra,
            train_device=device,
            job_id=job_id,
        )
        train_kw.update(remerged)

    max_batch = batch
    if imgsz >= 1280:
        max_batch = 4 if multi_scale else 8
    elif imgsz >= 1024:
        max_batch = 8
    if batch > max_batch:
        train_log.write(
            f"train_safe_profile job={job_id} cap batch {batch} -> {max_batch} (imgsz={imgsz})"
        )
        train_kw["batch"] = max_batch
        batch = max_batch

    adjusted_batch = _adjust_batch_for_single_sample_tail(
        batch=int(train_kw.get("batch") or batch),
        train_sample_count=train_sample_count,
    )
    if adjusted_batch != int(train_kw.get("batch") or batch):
        train_log.write(
            "train_safe_profile job="
            f"{job_id} adjust batch {train_kw.get('batch')} -> {adjusted_batch} "
            f"(avoid single-sample tail, train_samples={train_sample_count})"
        )
        train_kw["batch"] = adjusted_batch

    if imgsz >= 1024:
        stable_workers = min(
            _effective_single_gpu_train_workers(
                train_kw.get("workers"),
                imgsz=imgsz,
                batch=int(train_kw.get("batch") or 16),
            ),
            _MAX_LARGE_IMAGE_TRAIN_WORKERS,
        )
        if int(train_kw.get("workers") or 0) != stable_workers:
            train_log.write(
                f"train_safe_profile job={job_id} cap workers -> {stable_workers} "
                f"(large_imgsz stability, was {train_kw.get('workers')})"
            )
            train_kw["workers"] = stable_workers
        desired_cache = _default_train_cache_mode(imgsz=imgsz, train_device=device)
        if train_kw.get("cache") != desired_cache:
            train_log.write(
                f"train_safe_profile job={job_id} set cache -> {desired_cache} "
                f"(large_imgsz stability, was {train_kw.get('cache')})"
            )
            train_kw["cache"] = desired_cache
    if imgsz >= 1280 and _truthy_train_flag(train_kw.get("amp")):
        train_log.write(
            f"train_safe_profile job={job_id} disable amp (large_imgsz={imgsz} stability)"
        )
        train_kw["amp"] = False

    return device


def _is_nothing_to_resume_error(exc: BaseException) -> bool:
    return yolo_resume.is_nothing_to_resume_error(exc)


def _model_train_with_retry(
    model: Any,
    train_kw: dict[str, Any],
    *,
    train_log: Any,
    job_id: int,
    work_root: Optional[Path] = None,
) -> Any:
    from labelu.internal.common.pipeline_process_log import tee_stdio

    base_batch = int(train_kw.get("batch") or 16)
    batches = _batch_fallback_values(base_batch)
    last_exc: Optional[BaseException] = None
    resume_repair_tried = False
    for idx, batch in enumerate(batches):
        kw = dict(train_kw)
        kw["batch"] = batch
        try:
            train_log.write(
                "model.train attempt="
                f"{idx + 1}/{len(batches)} "
                f"device={kw.get('device')} batch={batch} imgsz={kw.get('imgsz')} amp={kw.get('amp')}"
            )
            with tee_stdio(train_log):
                return model.train(**kw)
        except Exception as exc:
            last_exc = exc
            resume_path = kw.get("resume")
            if (
                not resume_repair_tried
                and _is_nothing_to_resume_error(exc)
                and work_root is not None
                and isinstance(resume_path, (str, Path))
            ):
                last_pt = Path(resume_path)
                target_epochs = int(kw.get("epochs") or 0)
                if _repair_checkpoint_epoch_before_resume(
                    last_pt,
                    work_root,
                    target_epochs=target_epochs,
                    job_id=job_id,
                ):
                    resume_repair_tried = True
                    train_log.write(
                        "model.train nothing_to_resume: checkpoint epoch repaired, retry once"
                    )
                    continue
                _record_auto_resume_failure(job_id, str(exc)[:200])
            if idx + 1 >= len(batches) or not _is_retryable_train_failure(exc):
                if _is_nothing_to_resume_error(exc):
                    _record_auto_resume_failure(job_id, str(exc)[:200])
                raise
            train_log.write_block("train_retry", str(exc)[:800])
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"YOLO train job {job_id}: model.train produced no result")


def _merge_train_hyperparams(
    train_kw: dict,
    hp_extra: dict,
    *,
    train_device: str,
    job_id: int,
) -> dict:
    """合并 AI/用户超参。多卡 DDP 下禁止覆盖 workers/amp（避免 DDP 子进程异常）。"""
    merged = dict(train_kw)
    extra = dict(hp_extra) if hp_extra else {}
    if _is_multi_gpu_device(train_device):
        for key in ("workers", "amp"):
            if key in extra:
                logger.warning(
                    "YOLO train job {} ignore hyperparams {}={} for multi-GPU device={}",
                    job_id,
                    key,
                    extra.pop(key),
                    train_device,
                )
        if "cache" in extra:
            logger.warning(
                "YOLO train job {} ignore hyperparams cache={} for multi-GPU device={}",
                job_id,
                extra.pop("cache"),
                train_device,
            )
    if extra:
        merged.update(extra)
    return _apply_multi_gpu_train_runtime(
        merged,
        train_device=train_device,
        imgsz=int(merged.get("imgsz") or 640),
        batch=int(merged.get("batch") or 16),
    )


def _resume_train_hyperparams(
    cmd: CreateYoloTrainJobCommand,
    *,
    train_device: str,
    job_id: int,
) -> dict:
    return yolo_resume.resume_train_hyperparams(
        _merge_train_hyperparams,
        normalize_hyperparams,
        cmd,
        train_device=train_device,
        job_id=job_id,
    )


def _read_checkpoint_train_field(checkpoint_path: Path, field: str) -> Any:
    return yolo_resume.read_checkpoint_train_field(checkpoint_path, field)


def _strip_checkpoint_scaler(checkpoint_path: Path) -> bool:
    return yolo_resume.strip_checkpoint_scaler(checkpoint_path, logger)
    """amp=false 续训时移除空 GradScaler state，避免 Ultralytics 加载失败。"""


def _patch_checkpoint_train_args(
    checkpoint_path: Path,
    updates: dict[str, Any],
) -> list[str]:
    return yolo_resume.patch_checkpoint_train_args(checkpoint_path, updates, logger)
    """Patch Ultralytics checkpoint args so resume can honor runtime hyperparams."""


def _build_resume_train_kwargs(
    *,
    last_path: Path,
    cmd: CreateYoloTrainJobCommand,
    train_device: str,
    job_id: int,
) -> dict:
    return yolo_resume.build_resume_train_kwargs(
        _repair_checkpoint_epoch_before_resume,
        _resume_train_hyperparams,
        _read_checkpoint_train_field,
        _patch_checkpoint_train_args,
        _strip_checkpoint_scaler,
        _truthy_train_flag,
        _append_train_process_log,
        _ULTRALYTICS_RESUME_OVERRIDE_KEYS,
        last_path=last_path,
        cmd=cmd,
        train_device=train_device,
        job_id=job_id,
    )


def _format_train_failure_detail(job_id: int, exc: Exception) -> str:
    return yolo_summary.format_train_failure_detail(_job_work_root, job_id, exc)
    """失败详情：保留异常摘要，并提示作业目录内 Ultralytics 日志位置。"""
    base = str(exc).strip() or exc.__class__.__name__
    train_dir = _job_work_root(job_id) / "train"
    if train_dir.is_dir():
        return f"{base[:420]} | 详见 {train_dir}"
    return base[:500]


def _read_train_phase(
    job_id: int,
) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    return yolo_summary.read_train_phase(_job_work_root, _TRAIN_PHASE_LABELS, job_id)
    path = _job_work_root(job_id) / "phase.json"
    if not path.is_file():
        return None, None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    phase = str(data.get("phase") or "").strip() or None
    detail = str(data.get("detail") or "").strip()
    runtime_pid: Optional[int] = None
    try:
        if data.get("runtime_pid") is not None:
            runtime_pid = int(data.get("runtime_pid"))
    except (TypeError, ValueError):
        runtime_pid = None
    label = _TRAIN_PHASE_LABELS.get(phase or "", phase or "")
    if detail:
        label = f"{label}（{detail}）"
    return phase, label or None, runtime_pid


def _results_csv_progress(job_id: int) -> Tuple[Optional[int], Optional[str]]:
    return yolo_summary.results_csv_progress(
        settings.MEDIA_ROOT,
        settings.YOLO_TRAIN_WORK_SUBDIR,
        job_id,
    )
    csv_path = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "train"
        / "results.csv"
    )
    if not csv_path.is_file():
        return None, None
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return None, None
    if len(rows) < 2:
        return None, None
    header = rows[0]
    last = rows[-1]
    current_epoch: Optional[int] = None
    try:
        if "epoch" in header:
            ei = header.index("epoch")
            if ei < len(last):
                current_epoch = int(float(last[ei]))
    except (ValueError, IndexError):
        pass
    snap = ",".join(last)
    if len(snap) > 400:
        snap = snap[:400] + "…"
    return current_epoch, snap


def _float_metric(v: Any) -> Optional[float]:
    return yolo_summary.float_metric(v)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _training_val_metrics_for_job(
    job: YoloTrainJob,
) -> Optional[YoloTrainingValMetrics]:
    train_dir = resolve_train_dir_from_weights(job.weights_path)
    contract = metrics_contract_from_summary(job.metrics_summary, train_dir=train_dir)
    fm = contract.get("authoritative_val_metrics")
    if fm is None:
        return None
    source = str(contract.get("authoritative_source") or "none")
    ep_raw = fm.get("epoch")
    epoch_i: Optional[int] = None
    if ep_raw is not None:
        try:
            epoch_i = int(float(ep_raw))
        except (TypeError, ValueError):
            epoch_i = None
    return YoloTrainingValMetrics(
        map50_95=_float_metric(fm.get("map50_95")),
        map50=_float_metric(fm.get("map50")),
        precision=_float_metric(fm.get("precision")),
        recall=_float_metric(fm.get("recall")),
        overall_recall=_float_metric(fm.get("overall_recall")),
        overall_recall_threshold=_float_metric(fm.get("overall_recall_threshold")),
        fitness=_float_metric(fm.get("fitness")),
        epoch=epoch_i,
        summary_line=format_training_metrics_line(fm),
        source=source,
    )


def _validation_pass_row(
    tm: Optional[YoloTrainingValMetrics],
) -> YoloValidationPassAssessment:
    return yolo_summary.validation_pass_row(
        validation_pass_assessment,
        YoloValidationPassAssessment,
        settings,
        tm,
    )
    if not tm:
        return YoloValidationPassAssessment(
            status="无验证指标",
            detail="未读取到验证指标（metrics_summary / results.csv）",
        )
    d = validation_pass_assessment(
        tm.map50_95,
        tm.map50,
        threshold_map5095=float(settings.YOLO_VAL_PASS_MAP5095),
        threshold_map50=float(settings.YOLO_VAL_PASS_MAP50),
    )
    return YoloValidationPassAssessment(status=str(d["status"]), detail=d.get("detail"))


def _is_plate_recognition_job(job: YoloTrainJob) -> bool:
    return str(getattr(job, "job_type", None) or "").strip() == "plate_recognition"


def _is_plate_detection_job(job: YoloTrainJob) -> bool:
    return str(getattr(job, "job_type", None) or "").strip() == "plate_detection"


def _is_plate_job(job: YoloTrainJob) -> bool:
    return _is_plate_detection_job(job) or _is_plate_recognition_job(job)


def _read_plate_progress_csv(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return {}
    if not rows:
        return {}
    row = rows[-1]
    try:
        epoch = int(float(row.get("epoch") or 0))
    except (TypeError, ValueError):
        epoch = 0
    try:
        updated_at = path.stat().st_mtime
    except OSError:
        updated_at = None
    return {
        "current_epoch": epoch,
        "snapshot": ",".join(str(value or "") for value in row.values()),
        "updated_at": updated_at,
    }


def _read_plate_progress_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    result = dict(value)
    result["snapshot"] = ""
    if result.get("loss") is not None:
        try:
            result["snapshot"] = f"loss={float(result['loss']):.4f}"
        except (TypeError, ValueError):
            pass
    return result


def _plate_live_component_progress(job: YoloTrainJob) -> dict[str, dict[str, Any]]:
    root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{int(getattr(job, 'id', 0))}"
    )
    target_epochs = int(getattr(job, "epochs", 0) or 0)
    component_root = (
        "plate_detection" if _is_plate_detection_job(job) else "plate_components"
    )
    progress_dir = root / component_root / "progress"
    result: dict[str, dict[str, Any]] = {}
    components = (
        ("pose",)
        if _is_plate_detection_job(job)
        else ("pose", "ocr", "color", "plate_type")
    )
    for component in components:
        current: dict[str, Any] = {"target_epochs": target_epochs}
        if component == "pose":
            candidates = [
                root / component_root / "pose_train" / "train" / "results.csv",
                root / component_root / "pose_final_fit" / "train" / "results.csv",
            ]
            existing = [path for path in candidates if path.is_file()]
            if existing:
                try:
                    latest = max(existing, key=lambda p: p.stat().st_mtime)
                except OSError:
                    latest = existing[-1]
                current.update(_read_plate_progress_csv(latest))
        else:
            current.update(
                _read_plate_progress_json(progress_dir / f"{component}.json")
            )
        result[component] = current
    return result


def _plate_summary_fields(job: YoloTrainJob) -> dict[str, Any]:
    if not _is_plate_job(job):
        return {
            "component_progress": None,
            "component_metrics": None,
            "composite_assessment": None,
        }
    component_definitions = (
        yolo_plate_summary.PLATE_DETECTION_COMPONENT_DEFINITIONS
        if _is_plate_detection_job(job)
        else yolo_plate_summary.COMPONENT_DEFINITIONS
    )
    metrics = yolo_plate_summary.component_metrics(
        job.metrics_summary,
        component_definitions=component_definitions,
    )
    progress = yolo_plate_summary.component_progress(
        getattr(job, "component_status_json", None),
        job.metrics_summary,
        _plate_live_component_progress(job),
        component_definitions=component_definitions,
    )
    if _is_plate_detection_job(job):
        return {
            "component_progress": progress,
            "component_metrics": metrics,
            "composite_assessment": None,
        }
    assessment = yolo_plate_summary.composite_assessment(
        job.metrics_summary,
        pose_map50_threshold=float(settings.YOLO_VAL_PASS_MAP50),
        pose_map50_95_threshold=float(settings.YOLO_VAL_PASS_MAP5095),
    )
    return {
        "component_progress": progress,
        "component_metrics": metrics,
        "composite_assessment": assessment,
    }


def _validation_pass_for_job(job: YoloTrainJob) -> YoloValidationPassAssessment:
    if _is_plate_recognition_job(job):
        assessment = _plate_summary_fields(job)["composite_assessment"]
        if assessment:
            return YoloValidationPassAssessment(
                status=str(assessment["status"]),
                detail=str(assessment["summary"]),
            )
        return YoloValidationPassAssessment(
            status="无验证指标",
            detail="未读取到复合车牌四组件验证指标",
        )
    return _validation_pass_row(_training_val_metrics_for_job(job))


def _composite_training_assessment_for_job(
    job: YoloTrainJob,
) -> Optional[TrainingAssessmentResponse]:
    assessment = _plate_summary_fields(job)["composite_assessment"]
    if not assessment:
        return None
    status_value = str(assessment["status"])
    if status_value == "达标":
        code, label = "passed", "达标"
    elif status_value == "不达标":
        code, label = "failed", "不达标"
    elif status_value == "需复核":
        code, label = "conditional_passed", "条件达标"
    else:
        code, label = "pending", "待验证"
    failed_keys = [
        str(item.get("key"))
        for item in assessment.get("checks") or []
        if item.get("status") in {"不达标", "待评估"}
    ]
    if assessment.get("warnings"):
        failed_keys.append("dataset_class_coverage_warning")
    return TrainingAssessmentResponse(
        status_code=code,
        status_label=label,
        reason_codes=failed_keys,
        reason_summary=str(assessment["summary"]),
        summary=str(assessment["summary"]),
        evidence=assessment,
    )


def _sample_counts_for_job(job: YoloTrainJob) -> Dict[str, Optional[int]]:
    if _is_plate_job(job):
        counts = yolo_plate_summary.sample_counts(
            getattr(job, "dataset_manifest_json", None)
        )
        if any(counts.get(split) is not None for split in ("train", "val", "test")):
            return counts
    return yolo_summary.sample_counts_for_job(resolve_job_sample_counts, job)
    return resolve_job_sample_counts(
        job_id=job.id,
        metrics_summary=job.metrics_summary,
        train_sample_count=job.train_sample_count,
        val_sample_count=job.val_sample_count,
    )


def _test_sample_count_for_job(job: YoloTrainJob) -> Optional[int]:
    return yolo_summary.test_sample_count_for_job(_sample_counts_for_job, job)
    return _sample_counts_for_job(job).get("test")


def _test_val_metrics_for_job(job: YoloTrainJob) -> Optional[YoloTestValMetrics]:
    metrics = yolo_summary.test_val_metrics_for_job(
        read_test_val_metrics_from_summary,
        format_training_metrics_line,
        _float_metric,
        YoloTestValMetrics,
        job,
    )
    if not metrics or metrics.status != "ok" or metrics.map50_95 is None:
        return metrics
    updates: Dict[str, Any] = {}
    test_sample_count = _test_sample_count_for_job(job)
    if (metrics.images or 0) <= 0 and (test_sample_count or 0) > 0:
        updates["images"] = int(test_sample_count)
    if metrics.instances is not None and metrics.instances <= 0:
        updates["instances"] = None
    return metrics.copy(update=updates) if updates else metrics
    tvm = read_test_val_metrics_from_summary(job.metrics_summary)
    if not tvm:
        return None
    st = str(tvm.get("status") or "unknown")
    line: Optional[str] = None
    if st == "ok":
        line = format_training_metrics_line(
            {
                "map50_95": tvm.get("map50_95"),
                "map50": tvm.get("map50"),
                "precision": tvm.get("precision"),
                "recall": tvm.get("recall"),
            }
        )
    return YoloTestValMetrics(
        status=st,
        split=str(tvm.get("split") or "test"),
        map50_95=_float_metric(tvm.get("map50_95")),
        map50=_float_metric(tvm.get("map50")),
        precision=_float_metric(tvm.get("precision")),
        recall=_float_metric(tvm.get("recall")),
        images=int(tvm["images"]) if tvm.get("images") is not None else None,
        instances=int(tvm["instances"]) if tvm.get("instances") is not None else None,
        summary_line=line,
        detail=str(tvm["detail"])[:500] if tvm.get("detail") else None,
        run_at=str(tvm["run_at"]) if tvm.get("run_at") else None,
    )


def _val_test_comparison_for_job(job: YoloTrainJob) -> Optional[YoloValTestComparison]:
    contract = metrics_contract_from_summary(
        job.metrics_summary,
        train_dir=resolve_train_dir_from_weights(job.weights_path),
    )
    val_fm = contract.get("authoritative_val_metrics")
    test_raw = read_test_val_metrics_from_summary(job.metrics_summary)
    test_fm = test_raw if test_raw and test_raw.get("status") == "ok" else None
    cmp = build_val_test_comparison(val_fm, test_fm)
    if not cmp:
        return None
    return YoloValTestComparison(**cmp)


def _creator_emails_by_user_ids(db: Session, user_ids: Iterable[int]) -> dict[int, str]:
    from labelu.internal.domain.models.user import User

    return yolo_summary.creator_emails_by_user_ids(db, User, user_ids)
    """批量解析训练/导入作业的创建者登录账号（User.username，一般为邮箱）。"""
    ids = {int(uid) for uid in user_ids if uid}
    if not ids:
        return {}
    from labelu.internal.domain.models.user import User

    rows = db.query(User.id, User.username).filter(User.id.in_(ids)).all()
    return {int(uid): (uname or "").strip() for uid, uname in rows}


def _latest_edge_validation_for_job(job: YoloTrainJob):
    try:
        from labelu.internal.application.service import (
            yolo_aicm_publish as publish_service,
        )

        with session_scope() as db:
            exports = crud_yolo_bm1688_export.list_for_job(
                db, yolo_train_job_id=int(job.id), limit=1
            )
            if not exports:
                return None
            export_id = int(exports[0].id)
            return publish_service._edge_validation_status_for_export(
                db, export_id=export_id
            )
    except Exception:
        return None


def _classes_fallback_for_job(job: YoloTrainJob) -> List[str]:
    return yolo_summary.classes_fallback_for_job(
        read_class_names_from_data_yaml,
        _job_work_root,
        session_scope,
        crud_task,
        build_yolo_rect_class_names,
        _parse_task_config,
        job,
    )
    """历史作业：data.yaml → 任务 rectTool 配置，保证前端能列出全部预设标签。"""


def _checkpoint_epoch_blocks_resume(
    checkpoint_path: Path,
    work_root: Path,
    *,
    target_epochs: int,
) -> bool:
    return yolo_resume.checkpoint_epoch_blocks_resume(
        checkpoint_path,
        work_root,
        target_epochs=target_epochs,
        read_last_epoch_from_results_csv_fn=read_last_epoch_from_results_csv,
    )
    """
    Ultralytics 在部署中断等场景可能把 checkpoint epoch 写成 -1（表示已结束），
    但 results.csv 尚未训满，此时 resume=True 会报 nothing to resume。
    """


def _repair_checkpoint_epoch_before_resume(
    checkpoint_path: Path,
    work_root: Path,
    *,
    target_epochs: int,
    job_id: int,
) -> bool:
    return yolo_resume.repair_checkpoint_epoch_before_resume(
        _checkpoint_epoch_blocks_resume,
        read_last_epoch_from_results_csv,
        _append_train_process_log,
        logger,
        checkpoint_path,
        work_root,
        target_epochs=target_epochs,
        job_id=job_id,
    )
    """将异常 checkpoint epoch 对齐到 results.csv，便于 Ultralytics 正常 resume。"""


def _is_ultralytics_train_loop_complete(work_root: Path, target_epochs: int) -> bool:
    return yolo_summary.is_ultralytics_train_loop_complete(
        read_last_epoch_from_results_csv,
        work_root,
        target_epochs,
    )
    """results.csv 末行 epoch 已达目标且存在 best.pt 时，无需再调用 model.train(resume=True)。"""
    if target_epochs <= 0:
        return False
    train_dir = work_root / "train"
    best_path = train_dir / "weights" / "best.pt"
    if not best_path.is_file():
        return False
    last_ep = read_last_epoch_from_results_csv(train_dir)
    if last_ep is None:
        return False
    return int(last_ep) >= int(target_epochs)


def _finalize_yolo_train_job_sync(
    *,
    job_id: int,
    work_root: Path,
    dataset_root: Path,
    cmd: CreateYoloTrainJobCommand,
    train_n: int,
    val_n: int,
    test_n: int,
    classes: List[str],
    source_ids: list[int],
    per_task_counts: dict[int, int],
    per_task_exported: dict[int, int],
    results: Any = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    """训练循环结束后：写 metrics_summary、按类 val/test、标记 COMPLETED。"""
    _raise_if_stop_requested(job_id)
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if is_stop_requested(job_id) or _job_is_cancelled(job):
            _mark_job_cancelled(
                job_id,
                db=db,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            return

        best_path = Path(work_root) / "train" / "weights" / "best.pt"
        if not best_path.is_file():
            raise RuntimeError("训练结束但未找到 best.pt")
        last_path = best_path.parent / "last.pt"
        _append_train_process_log(
            job_id,
            f"finalize weights_ready best.pt path={best_path.resolve()}",
        )
        if last_path.is_file():
            try:
                last_bytes = last_path.stat().st_size
            except OSError:
                last_bytes = -1
            _append_train_process_log(
                job_id,
                f"finalize weights_ready last.pt path={last_path.resolve()} bytes={last_bytes}",
            )

        metrics_summary = ""
        train_dir = work_root / "train"
        last_epoch_metrics = parse_results_csv_final_metrics(train_dir)
        best_epoch_metrics = parse_results_csv_best_metrics(train_dir)
        final_metrics = last_epoch_metrics
        class_names = _dataset_class_names_for_summary(
            job, dataset_root, prepared_classes=classes or None
        )
        summary_obj: dict[str, Any] = {
            "metrics_contract_version": 2,
            "results_dir": str(train_dir),
            "dataset": {"train": train_n, "val": val_n, "test": test_n},
            "classes": class_names,
            "class_instance_counts": count_class_instances_in_dataset(
                dataset_root, class_names
            ),
            "final_metrics": final_metrics,
            "last_epoch_metrics": last_epoch_metrics,
            "best_epoch_metrics": best_epoch_metrics,
        }
        if source_ids:
            summary_obj["merge_tasks"] = _merge_tasks_summary_payload(
                source_ids,
                per_task_counts,
                per_task_exported or None,
            )
        if results is not None:
            summary_obj["speed"] = getattr(results, "speed", None)

        data_yaml_path = dataset_root / "data.yaml"
        _write_train_phase(
            job_id,
            "val_per_class",
            detail="split=val",
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
        val_detail = run_split_val(
            weights_path=best_path,
            data_yaml=data_yaml_path,
            split="val",
            imgsz=int(cmd.imgsz),
            device=_resolve_yolo_train_device(),
        )
        if val_detail.get("status") == "ok":
            best_pt_metrics = {
                key: val_detail.get(key)
                for key in (
                    "status",
                    "split",
                    "run_at",
                    "map50",
                    "map50_95",
                    "precision",
                    "recall",
                    "speed",
                    "images",
                    "instances",
                )
            }
            if best_epoch_metrics and best_epoch_metrics.get("epoch") is not None:
                best_pt_metrics["selected_epoch"] = best_epoch_metrics["epoch"]
            best_pt_metrics["weights"] = "best.pt"
            summary_obj["best_pt_val_metrics"] = best_pt_metrics
        if val_detail.get("status") == "ok" and val_detail.get("per_class_metrics"):
            summary_obj["per_class_val_metrics"] = val_detail["per_class_metrics"]
        elif val_detail.get("classes") and not summary_obj.get("classes"):
            summary_obj["classes"] = val_detail["classes"]

        _raise_if_stop_requested(job_id)
        if bool(settings.YOLO_TRAIN_AUTO_TEST_VAL) and test_n > 0:
            _write_train_phase(
                job_id,
                "test_val",
                detail="split=test",
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            test_val = run_split_val(
                weights_path=best_path,
                data_yaml=dataset_root / "data.yaml",
                split="test",
                imgsz=int(cmd.imgsz),
                device=_resolve_yolo_train_device(),
            )
            summary_obj["test_val_metrics"] = test_val
            logger.info(
                "YOLO train job {} test_val status={} map50={}",
                job_id,
                test_val.get("status"),
                test_val.get("map50"),
            )
        try:
            metrics_summary = json.dumps(summary_obj, ensure_ascii=False, default=str)
        except Exception:
            metrics_summary = str(summary_obj)

        if final_metrics:
            _append_train_process_log(
                job_id,
                (
                    "finalize final_metrics "
                    f"epoch={final_metrics.get('epoch')} "
                    f"mAP50={final_metrics.get('map50')} "
                    f"mAP50-95={final_metrics.get('map50_95')}"
                ),
            )
        try:
            best_bytes = best_path.stat().st_size
        except OSError:
            best_bytes = -1
        _append_train_process_log(
            job_id,
            f"finalize weights_saved best.pt bytes={best_bytes} path={best_path.resolve()}",
        )

        if not job:
            return
        with begin_transaction(db):
            _assert_train_result_write_fence(
                db,
                job_id=job_id,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            crud_yolo_train_job.update_status(
                db,
                job,
                YoloTrainStatus.COMPLETED.value,
                train_sample_count=train_n,
                val_sample_count=val_n,
                weights_path=str(best_path.resolve()),
                metrics_summary=metrics_summary or None,
            )
    _write_train_phase(
        job_id,
        "completed",
        execution_owner=execution_owner,
        execution_fencing_token=execution_fencing_token,
    )
    completed_best = Path(work_root) / "train" / "weights" / "best.pt"
    if completed_best.is_file():
        _append_train_process_log(
            job_id,
            f"phase=completed detail=best.pt ready at {completed_best.resolve()}",
        )
    _clear_auto_resume_backoff(job_id)
    if not _is_spawn_train_child():
        _run_post_train_side_effects(job_id)


def _dataset_class_names_for_summary(
    job: YoloTrainJob,
    dataset_root: Path,
    *,
    prepared_classes: Optional[List[str]] = None,
) -> List[str]:
    return yolo_summary.dataset_class_names_for_summary(
        read_class_names_from_data_yaml,
        _classes_fallback_for_job,
        job,
        dataset_root,
        prepared_classes=prepared_classes,
    )
    """训练结束写 metrics_summary 时的类别列表；resume 路径无 prepare 时需从 data.yaml 读取。"""
    if prepared_classes:
        return list(prepared_classes)
    names = read_class_names_from_data_yaml(dataset_root / "data.yaml")
    if names:
        return names
    return _classes_fallback_for_job(job)


def _class_metrics_for_job(job: YoloTrainJob) -> Optional[YoloClassMetricsDetail]:
    return yolo_summary.class_metrics_for_job(
        _classes_fallback_for_job,
        _job_work_root,
        count_class_instances_in_dataset,
        build_class_metrics_detail,
        YoloClassMetricRow,
        YoloClassMetricsDetail,
        job,
    )
    fallback = _classes_fallback_for_job(job)
    dataset_root = _job_work_root(job.id) / "dataset"
    fallback_counts = None
    if fallback and dataset_root.is_dir():
        fallback_counts = count_class_instances_in_dataset(dataset_root, fallback)
    raw_detail = build_class_metrics_detail(
        job.metrics_summary,
        fallback_classes=fallback or None,
        fallback_instance_counts=fallback_counts or None,
    )
    if not raw_detail:
        return None

    def _rows(key: str) -> List[YoloClassMetricRow]:
        return [YoloClassMetricRow(**row) for row in raw_detail.get(key) or []]

    return YoloClassMetricsDetail(
        classes=raw_detail.get("classes") or [],
        rows=_rows("rows"),
        val_metrics=_rows("val_metrics"),
        test_metrics=_rows("test_metrics"),
    )


def _metrics_fields_for_job(job: YoloTrainJob) -> dict:
    return yolo_summary.metrics_fields_for_job(
        _test_sample_count_for_job,
        _training_val_metrics_for_job,
        _test_val_metrics_for_job,
        _val_test_comparison_for_job,
        job,
    )
    return {
        "test_sample_count": _test_sample_count_for_job(job),
        "training_val_metrics": _training_val_metrics_for_job(job),
        "test_val_metrics": _test_val_metrics_for_job(job),
        "val_test_comparison": _val_test_comparison_for_job(job),
    }


def _dataset_validation_report_for_job(job: YoloTrainJob) -> dict[str, Any]:
    return yolo_summary.read_dataset_validation_report(_job_work_root, int(job.id))


def _merge_done_counts_from_job(job: YoloTrainJob) -> dict[int, int]:
    return yolo_summary.merge_done_counts_from_job(job)
    raw = getattr(job, "metrics_summary", None) or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        mt = (data or {}).get("merge_tasks") or {}
        counts = mt.get("done_counts") or {}
        if isinstance(counts, dict):
            return {int(k): int(v) for k, v in counts.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _merge_exported_counts_from_job(job: YoloTrainJob) -> dict[int, int]:
    return yolo_summary.merge_exported_counts_from_job(job)
    """数据集导出完成后各任务实际写入 train+val+test 的图片张数。"""
    raw = getattr(job, "metrics_summary", None) or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        mt = (data or {}).get("merge_tasks") or {}
        counts = mt.get("exported_counts") or {}
        if isinstance(counts, dict):
            return {int(k): int(v) for k, v in counts.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _merge_tasks_summary_payload(
    source_ids: list[int],
    per_task_done: dict[int, int],
    per_task_exported: dict[int, int] | None = None,
) -> dict:
    return yolo_summary.merge_tasks_summary_payload(
        source_ids,
        per_task_done,
        per_task_exported,
    )
    payload: dict = {
        "task_ids": source_ids,
        "done_counts": {str(k): int(v) for k, v in per_task_done.items()},
    }
    if per_task_exported:
        payload["exported_counts"] = {
            str(k): int(v) for k, v in per_task_exported.items()
        }
    return payload


def _runtime_fields_for_job(job: YoloTrainJob) -> dict[str, Any]:
    from labelu.internal.application.service.remote_yolo_train import (
        remote_job_runtime_status,
    )
    from labelu.internal.application.service.yolo_train_queue import (
        active_yolo_train_job_ids_on_gpu,
    )

    return yolo_summary.runtime_fields_for_job(
        _read_train_phase,
        _ACTIVE_TRAIN_JOBS,
        active_yolo_train_job_ids_on_gpu,
        _runtime_train_process_on_gpu,
        remote_job_runtime_status,
        job,
        completed_status=YoloTrainStatus.COMPLETED.value,
        failed_status=YoloTrainStatus.FAILED.value,
        cancelled_status=YoloTrainStatus.CANCELLED.value,
        pending_status=YoloTrainStatus.PENDING.value,
        processing_status=YoloTrainStatus.PROCESSING.value,
    )
    phase, _label, runtime_pid, _detail = _read_train_phase(job.id)
    status_value = str(job.status or "")
    runtime_status: Optional[str] = None
    restartable = False
    restart_reason: Optional[str] = None

    if status_value == YoloTrainStatus.COMPLETED.value:
        runtime_status = "COMPLETED"
    elif status_value == YoloTrainStatus.FAILED.value:
        runtime_status = "FAILED"
        restartable = True
        restart_reason = "训练失败，可从 last.pt 继续运行"
    elif status_value == YoloTrainStatus.CANCELLED.value:
        runtime_status = "CANCELLED"
        restartable = True
        restart_reason = "训练已暂停，可从 last.pt 继续运行"
    elif status_value == YoloTrainStatus.PENDING.value:
        if job.id in _ACTIVE_TRAIN_JOBS:
            runtime_status = "RUNNING"
        elif str(getattr(job, "execution_mode", None) or "local").strip() == "remote":
            from labelu.internal.application.service.remote_yolo_train import (
                remote_job_runtime_status,
            )

            remote_runtime = remote_job_runtime_status(job)
            if remote_runtime == "RUNNING":
                runtime_status = "RUNNING"
            else:
                runtime_status = "STOPPED"
                restartable = True
                restart_reason = "训练作业未在当前服务进程中运行"
        else:
            runtime_status = "STOPPED"
            restartable = True
            restart_reason = "训练作业未在当前服务进程中运行"
    elif status_value == YoloTrainStatus.PROCESSING.value:
        from labelu.internal.application.service.yolo_train_queue import (
            active_yolo_train_job_ids_on_gpu,
        )

        remote_runtime = None
        if str(getattr(job, "execution_mode", None) or "local").strip() == "remote":
            from labelu.internal.application.service.remote_yolo_train import (
                remote_job_runtime_status,
            )

            remote_runtime = remote_job_runtime_status(job)

        if remote_runtime == "RUNNING":
            runtime_status = "RUNNING"
        elif remote_runtime == "FAILED":
            runtime_status = "FAILED"
            restartable = True
            restart_reason = "远程训练失败，请查看 process.log 或 remote_status"
        elif remote_runtime == "STOPPED":
            runtime_status = "STOPPED"
            restartable = True
            restart_reason = "远程训练未在运行（可能上传中断或 Agent 未启动）"
        elif job.id in _ACTIVE_TRAIN_JOBS:
            runtime_status = "RUNNING"
        elif int(job.id) in active_yolo_train_job_ids_on_gpu():
            runtime_status = "RUNNING"
        elif phase == "running" and runtime_pid is not None:
            if _runtime_train_process_on_gpu(runtime_pid):
                runtime_status = "RUNNING"
            else:
                runtime_status = "STOPPED"
                restartable = True
                restart_reason = f"训练进程 PID {runtime_pid}（及其子进程）未出现在 NVIDIA 计算进程列表中"
        else:
            runtime_status = "STOPPED"
            restartable = True
            restart_reason = "训练作业未在当前服务进程中运行"

    return {
        "runtime_status": runtime_status,
        "runtime_pid": runtime_pid,
        "is_restartable": restartable,
        "restart_reason": restart_reason,
    }


def _job_is_actually_training(job: YoloTrainJob) -> bool:
    """是否仍不允许删除：终态（含用户已暂停）可删；仅排队/训练中且进程仍在跑时拦截。"""
    if job.status in (
        YoloTrainStatus.CANCELLED.value,
        YoloTrainStatus.COMPLETED.value,
        YoloTrainStatus.FAILED.value,
    ):
        return False
    if int(job.id) in _ACTIVE_TRAIN_JOBS:
        return True
    if str(getattr(job, "execution_mode", None) or "local").strip() == "remote":
        from labelu.internal.application.service.remote_yolo_train import (
            is_remote_train_job_dispatching,
            remote_job_runtime_status,
        )

        if is_remote_train_job_dispatching(int(job.id)):
            return True
        return remote_job_runtime_status(job) == "RUNNING"
    return _runtime_fields_for_job(job).get("runtime_status") == "RUNNING"


def _detach_train_job_runtime(job_id: int) -> None:
    """删除前解除内存登记并再次请求停止，避免后台线程与磁盘清理竞态。"""
    request_stop_train_job(job_id)
    _ACTIVE_TRAIN_JOBS.discard(int(job_id))


def _job_hyperparams_dict(job: YoloTrainJob) -> Optional[dict]:
    return yolo_hyperparams.job_hyperparams_dict(job)
    raw = getattr(job, "hyperparams_json", None)
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def _merge_runtime_hyperparams_into_job(
    job: YoloTrainJob,
    runtime_hyperparams: dict[str, Any],
) -> tuple[int, int, dict[str, Any]]:
    return yolo_hyperparams.merge_runtime_hyperparams_into_job(
        _job_hyperparams_dict,
        job,
        runtime_hyperparams,
    )
    merged_hp = dict(_job_hyperparams_dict(job) or {})
    epochs = int(job.epochs or 50)
    imgsz = int(job.imgsz or 640)
    for key, value in runtime_hyperparams.items():
        if key == "epochs":
            epochs = int(value)
        elif key == "imgsz":
            imgsz = int(value)
        else:
            merged_hp[key] = value
    return epochs, imgsz, merged_hp


def _apply_runtime_hyperparams_to_job_row(
    db: Session,
    job: YoloTrainJob,
    runtime_hyperparams: dict[str, Any],
) -> YoloTrainJob:
    return yolo_hyperparams.apply_runtime_hyperparams_to_job_row(
        _merge_runtime_hyperparams_into_job,
        normalize_hyperparams,
        db,
        job,
        runtime_hyperparams,
    )
    epochs, imgsz, merged_hp = _merge_runtime_hyperparams_into_job(
        job,
        runtime_hyperparams,
    )
    job.epochs = max(1, min(3000, epochs))
    job.imgsz = max(320, min(1280, imgsz))
    job.hyperparams_json = (
        json.dumps(normalize_hyperparams(merged_hp), ensure_ascii=False)
        if merged_hp
        else None
    )
    db.flush()
    db.refresh(job)
    return job


def _apply_pending_runtime_hyperparams_and_restart(
    job_id: int,
    *,
    execution_owner: str | None = None,
    execution_fencing_token: int | None = None,
) -> bool:
    def _assert_current(db: Session, job: YoloTrainJob) -> None:
        _assert_train_result_write_fence(
            db,
            job_id=int(job.id),
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )

    return yolo_hyperparams.apply_pending_runtime_hyperparams_and_restart(
        read_pending_runtime_hyperparams,
        normalize_runtime_editable_hyperparams,
        session_scope,
        begin_transaction,
        crud_yolo_train_job,
        _apply_runtime_hyperparams_to_job_row,
        _clear_pending_runtime_hyperparams,
        clear_stop_train_job,
        clear_user_pause_requested,
        _append_train_process_log,
        _launch_train_job_from_existing_config,
        job_id,
        before_apply_fn=_assert_current,
    )
    pending = read_pending_runtime_hyperparams(job_id)
    runtime_hp = normalize_runtime_editable_hyperparams(
        (pending or {}).get("hyperparams") if isinstance(pending, dict) else None
    )
    if not runtime_hp:
        return False
    with session_scope() as db:
        with begin_transaction(db):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                return False
            _apply_runtime_hyperparams_to_job_row(db, job, runtime_hp)
        _clear_pending_runtime_hyperparams(job_id)
        clear_stop_train_job(job_id)
        clear_user_pause_requested(job_id)
        _append_train_process_log(
            job_id,
            f"runtime_hyperparams_apply detail={json.dumps(runtime_hp, ensure_ascii=False)}",
        )
        return _launch_train_job_from_existing_config(db, job_id)


def _consume_pending_runtime_hyperparams_for_current_run(
    job_id: int,
    *,
    execution_owner: str | None = None,
    execution_fencing_token: int | None = None,
) -> Optional[CreateYoloTrainJobCommand]:
    from labelu.internal.application.service.yolo_train_queue import command_from_job

    def _assert_current(db: Session, job: YoloTrainJob) -> None:
        _assert_train_result_write_fence(
            db,
            job_id=int(job.id),
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )

    return yolo_hyperparams.consume_pending_runtime_hyperparams_for_current_run(
        read_pending_runtime_hyperparams,
        normalize_runtime_editable_hyperparams,
        session_scope,
        begin_transaction,
        crud_yolo_train_job,
        _apply_runtime_hyperparams_to_job_row,
        command_from_job,
        _clear_pending_runtime_hyperparams,
        clear_stop_train_job,
        clear_user_pause_requested,
        _append_train_process_log,
        job_id,
        before_apply_fn=_assert_current,
    )
    pending = read_pending_runtime_hyperparams(job_id)
    runtime_hp = normalize_runtime_editable_hyperparams(
        (pending or {}).get("hyperparams") if isinstance(pending, dict) else None
    )
    if not runtime_hp:
        return None
    with session_scope() as db:
        with begin_transaction(db):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                return None
            _apply_runtime_hyperparams_to_job_row(db, job, runtime_hp)
            source_ids = parse_source_task_ids(job)
            cmd = CreateYoloTrainJobCommand(
                model_weights=job.model_weights or "yolo11n.pt",
                weights_source=None,
                epochs=int(job.epochs or 50),
                imgsz=int(job.imgsz or 640),
                category=getattr(job, "category", None) or "",
                hyperparams=_job_hyperparams_dict(job),
                merge_task_ids=source_ids[1:] if len(source_ids) > 1 else None,
            )
        _clear_pending_runtime_hyperparams(job_id)
        clear_stop_train_job(job_id)
        clear_user_pause_requested(job_id)
        _append_train_process_log(
            job_id,
            f"runtime_hyperparams_apply_current_run detail={json.dumps(runtime_hp, ensure_ascii=False)}",
        )
        return cmd


def _remote_fields_for_job(job: YoloTrainJob) -> dict:
    from labelu.internal.common.remote_train_nodes_config import get_node_by_id

    return yolo_summary.remote_fields_for_job(get_node_by_id, job)
    mode = str(getattr(job, "execution_mode", None) or "local").strip() or "local"
    node_id = getattr(job, "train_node_id", None) or None
    node_name: Optional[str] = None
    remote_status: Optional[dict] = None
    remote_phase: Optional[str] = None
    if node_id:
        from labelu.internal.common.remote_train_nodes_config import get_node_by_id

        node = get_node_by_id(str(node_id))
        if node:
            node_name = str(node.get("name") or node_id)
    raw = getattr(job, "remote_status_json", None) or ""
    if raw:
        try:
            remote_status = json.loads(raw)
            if isinstance(remote_status, dict):
                remote_phase = (
                    str(
                        remote_status.get("remote_phase")
                        or remote_status.get("phase")
                        or ""
                    ).strip()
                    or None
                )
            else:
                remote_status = None
        except (TypeError, json.JSONDecodeError):
            remote_status = None
    remote_upload_pct: Optional[int] = None
    if isinstance(remote_status, dict):
        phase_key = (
            str(remote_status.get("remote_phase") or remote_status.get("phase") or "")
            .strip()
            .lower()
        )
        if phase_key in ("uploading", "downloading"):
            raw_pct = remote_status.get("upload_pct")
            if raw_pct is not None:
                try:
                    remote_upload_pct = int(raw_pct)
                except (TypeError, ValueError):
                    remote_upload_pct = None
            if remote_upload_pct is None:
                ub = remote_status.get("upload_bytes")
                ut = remote_status.get("upload_total_bytes")
                try:
                    if ub is not None and ut:
                        remote_upload_pct = min(100, int(int(ub) * 100 / int(ut)))
                except (TypeError, ValueError, ZeroDivisionError):
                    remote_upload_pct = None
    return {
        "execution_mode": mode,
        "train_node_id": node_id,
        "train_node_name": node_name,
        "remote_status": remote_status,
        "remote_phase": remote_phase,
        "remote_upload_pct": remote_upload_pct,
    }


def _local_gpu_fields_for_job(job: YoloTrainJob) -> dict[str, Optional[str]]:
    from labelu.internal.common.yolo_train_gpu_allocator import (
        gpu_display_fields_for_job,
    )

    return yolo_summary.local_gpu_fields_for_job(gpu_display_fields_for_job, job)
    mode = str(getattr(job, "execution_mode", None) or "local").strip() or "local"
    if mode == "remote":
        return {
            "train_gpu_device": None,
            "train_gpu_mode": None,
            "train_gpu_label": None,
        }
    from labelu.internal.common.yolo_train_gpu_allocator import (
        gpu_display_fields_for_job,
    )

    fields = gpu_display_fields_for_job(int(job.id))
    return {
        "train_gpu_device": fields.get("train_gpu_device"),
        "train_gpu_mode": fields.get("train_gpu_mode"),
        "train_gpu_label": fields.get("train_gpu_label"),
    }


def _job_to_response(
    db: Session,
    job: YoloTrainJob,
    task=None,
    *,
    include_training_task_detail: bool = False,
) -> YoloTrainJobResponse:
    # Keep the pre-training-task positional form ``_job_to_response(job, task)``
    # usable for lightweight callers while production paths pass ``db`` first.
    if (
        job is not None
        and getattr(db, "task_id", None) is not None
        and getattr(job, "config", None) is not None
        and not hasattr(job, "status")
    ):
        db, job, task = None, db, job

    response = yolo_summary.job_to_response(
        _read_train_phase,
        _results_csv_progress,
        _metrics_fields_for_job,
        _validation_pass_for_job,
        _sample_counts_for_job,
        parse_source_task_ids,
        _merge_done_counts_from_job,
        _merge_exported_counts_from_job,
        _runtime_fields_for_job,
        merge_task_label,
        _job_hyperparams_dict,
        _class_metrics_for_job,
        _dataset_validation_report_for_job,
        _remote_fields_for_job,
        _local_gpu_fields_for_job,
        YoloTrainJobResponse,
        YoloTrainStatus.PROCESSING.value,
        YoloTrainStatus.PENDING.value,
        YoloTrainStatus.FAILED.value,
        YoloTrainStatus.CANCELLED.value,
        job,
    )
    stop_info = training_stop_info(
        job_id=job.id,
        status=getattr(job, "status", None),
        configured_epochs=getattr(job, "epochs", None),
        hyperparams=(
            _job_hyperparams_dict(job) if hasattr(job, "hyperparams_json") else None
        ),
        weights_path=getattr(job, "weights_path", None),
        metrics_summary=getattr(job, "metrics_summary", None),
    )
    response.actual_epochs = stop_info["actual_epochs"]
    response.train_stop = YoloTrainStopInfoResponse(**stop_info)
    response.job_type = str(getattr(job, "job_type", None) or "yolo_detection")
    plate_fields = _plate_summary_fields(job)
    response.component_progress = plate_fields["component_progress"]
    response.component_metrics = plate_fields["component_metrics"]
    response.composite_assessment = plate_fields["composite_assessment"]
    for field_name, response_name in (
        ("component_status_json", "component_status"),
        ("component_artifacts_json", "component_artifacts"),
    ):
        try:
            value = json.loads(getattr(job, field_name, None) or "{}") or None
        except (TypeError, ValueError, json.JSONDecodeError):
            value = None
        setattr(response, response_name, value)
    task_rect_preset_slug = None
    task_rect_classes = None
    if task is not None:
        task_cfg = _parse_task_config(task.config)
        response.task_name = getattr(task, "name", None)
        task_rect_classes = build_yolo_rect_class_names(task_cfg) or None
        task_rect_preset_slug = (
            str(task_cfg.get("defaultRectPresetId") or "").strip() or None
        )
    response.task_rect_preset_slug = task_rect_preset_slug
    response.project_id = getattr(job, "project_id", None) or getattr(
        task, "project_id", None
    )
    response.task_rect_classes = task_rect_classes
    response.strategy_task = _strategy_task_marker_for_train_job(db, int(job.id))
    try:
        response.dataset_manifest = (
            json.loads(getattr(job, "dataset_manifest_json", None) or "{}") or None
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        response.dataset_manifest = None
    edge_validation = _latest_edge_validation_for_job(job)
    assessment_payload = build_assessment_payload(
        training_val_metrics=response.training_val_metrics,
        test_val_metrics=response.test_val_metrics,
        val_test_comparison=response.val_test_comparison,
        total_sample_count=response.total_sample_count,
        edge_validation=edge_validation,
        rect_preset_slug=task_rect_preset_slug,
        quality_policy_snapshot=quality_policy_snapshot_for_job(job),
    )
    response.training_assessment = assessment_payload.training_assessment
    response.edge_validation_assessment = assessment_payload.edge_validation_assessment
    response.release_gate_assessment = assessment_payload.release_gate_assessment
    response.quality_assessment = assessment_payload.quality_assessment
    composite_training_assessment = _composite_training_assessment_for_job(job)
    if composite_training_assessment is not None:
        response.training_assessment = composite_training_assessment
    training_task_fields = _training_task_response_fields(
        db,
        job,
        getattr(task, "name", None),
        include_detail=include_training_task_detail,
    )
    response.training_task_id = training_task_fields["training_task_id"]
    response.training_task_name = training_task_fields["training_task_name"]
    response.training_task_mode = training_task_fields["training_task_mode"]
    response.training_task_display_subtitle = training_task_fields[
        "training_task_display_subtitle"
    ]
    response.training_task_detail = training_task_fields["training_task_detail"]
    response.dataset_category_id = training_task_fields["dataset_category_id"]
    response.dataset_category_key = training_task_fields["dataset_category_key"]
    response.dataset_category_name = training_task_fields["dataset_category_name"]
    for field_name, field_value in _job_contract_response_fields(job).items():
        setattr(response, field_name, field_value)
    # 训练详情也返回与训练列表相同的自动 BM1688 编译状态，页面只展示状态，
    # 不再要求用户手动登记模型或点击开始编译。
    if db is not None:
        latest_export = crud_yolo_bm1688_export.latest_by_job_ids(
            db, yolo_train_job_ids=[int(job.id)]
        ).get(int(job.id))
        for field_name, field_value in _bm1688_compile_fields(latest_export).items():
            setattr(response, field_name, field_value)
    return response


async def create_train_job(
    db: Session,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    current_user: User,
    *,
    idempotency_key: str | None = None,
    idempotency_request_hash: str | None = None,
    defer_dispatch: bool = False,
) -> YoloTrainJobResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    # Optional client retry protection for the一期 ordinary YOLO11 create
    # endpoint. Without the header, two intentionally separate experiments
    # with the same visible configuration remain valid.
    retry_key = normalize_idempotency_key(idempotency_key)
    if retry_key and len(retry_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="Idempotency-Key 不能超过 256 个字符",
        )
    retry_hash = None
    if retry_key:
        retry_hash = str(idempotency_request_hash or "").strip() or request_payload_hash(
            {"task_id": int(task_id), "command": cmd.dict()}
        )
    if retry_key:
        existing = crud_yolo_train_job.get_by_idempotency(
            db, creator_id=current_user.id, idempotency_key=retry_key
        )
        if existing is not None:
            if existing.idempotency_request_hash != retry_hash:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message="Idempotency-Key 已用于其他训练请求",
                )
            return _job_to_response(
                db, existing, task, include_training_task_detail=True
            )

    # Recipe references are now accepted by the unified form as well as this
    # compatibility endpoint. Resolve an active version here so direct legacy
    # callers cannot persist a dangling recipe lineage.
    requested_recipe_key = canonical_training_recipe_key(cmd.training_recipe_key)
    if requested_recipe_key:
        recipe_query = db.query(TrainingRecipe).filter(
            TrainingRecipe.recipe_key == requested_recipe_key,
            TrainingRecipe.status == "active",
            TrainingRecipe.deleted_at.is_(None),
        )
        if cmd.training_recipe_version is not None:
            recipe_query = recipe_query.filter(
                TrainingRecipe.version == int(cmd.training_recipe_version)
            )
        recipe = recipe_query.order_by(TrainingRecipe.version.desc()).first()
        if recipe is None:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="训练配方不存在或已停用",
            )
        ensure_phase_one_training_job_type(getattr(recipe, "job_type", None))
        cmd = cmd.copy(
            update={
                "training_recipe_key": recipe.recipe_key,
                "training_recipe_version": int(recipe.version),
            }
        )

    requested_preset_slug = str(cmd.rect_preset_slug or "").strip()
    requested_preset = (
        crud_annotation_taxonomy.label_preset_get_by_slug(db, requested_preset_slug)
        if requested_preset_slug
        else None
    )
    if requested_preset and requested_preset.training_profile == "plate_recognition_v1":
        if retry_key:
            raise LabelUException(
                code=ErrorCode.CODE_30004_PHASE_ONE_CAPABILITY_RETIRED,
                status_code=status.HTTP_409_CONFLICT,
                message="历史复合车牌兼容训练不支持一期幂等提交，请使用普通矩形框训练主链",
            )
        from labelu.internal.application.command.training_task import (
            CreateLabelDimensionTrainingTaskCommand,
        )

        source_task_ids = [task_id, *(cmd.merge_task_ids or [])]
        source_task_ids = list(dict.fromkeys(int(value) for value in source_task_ids))
        base_name = (cmd.category or "").strip() or f"{task.name or '车牌'}-识别训练"
        candidate_name = base_name[:128]
        suffix = 2
        while not training_task_service.check_training_task_name_available(
            db, current_user.id, candidate_name
        ):
            suffix_text = f"-v{suffix}"
            candidate_name = f"{base_name[:128 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        created = training_task_service.create_label_dimension_training_task(
            db,
            current_user=current_user,
            cmd=CreateLabelDimensionTrainingTaskCommand(
                name=candidate_name,
                selected_presets=[{"preset_slug": requested_preset.slug}],
                selected_label_items=[
                    {
                        "preset_slug": requested_preset.slug,
                        "label_value": str(item.label_value or "").strip(),
                    }
                    for item in requested_preset.items
                    if str(item.label_value or "").strip()
                ],
                selected_task_ids=source_task_ids,
                model_weights=cmd.model_weights,
                weights_source=cmd.weights_source or "preset",
                weights_ref_id=cmd.weights_ref_id,
                epochs=cmd.epochs,
                imgsz=cmd.imgsz,
                hyperparams=cmd.hyperparams or {},
                plate_training_target=cmd.plate_training_target,
            ),
        )
        created_job = crud_yolo_train_job.get(db, created.latest_job_id)
        return _job_to_response(
            db,
            created_job,
            task,
            include_training_task_detail=True,
        )

    source_ids, per_task_counts = resolve_source_task_ids_for_create(
        db, primary_task_id=task_id, cmd=cmd, current_user=current_user
    )
    iteration_manifest = {}
    raw_hyperparams = cmd.hyperparams or {}
    if isinstance(raw_hyperparams, dict):
        candidate_manifest = raw_hyperparams.get("dataset_manifest")
        if isinstance(candidate_manifest, dict):
            iteration_manifest = candidate_manifest
    iteration_label_signature = (
        str(iteration_manifest.get("label_set_signature") or "").strip() or None
    )
    iteration_order_signature = (
        str(iteration_manifest.get("class_order_signature") or "").strip() or None
    )
    if cmd.weights_source == "auto":
        from labelu.internal.application.service.training_iteration import (
            resolve_auto_iteration_weights,
        )

        cmd = resolve_auto_iteration_weights(
            db,
            current_user=current_user,
            task_id=task_id,
            cmd=cmd,
            source_task_ids=source_ids,
            expected_label_set_signature=iteration_label_signature,
            expected_class_order_signature=iteration_order_signature,
        )

    # Direct legacy YOLO creates can now participate in the same model-family
    # contract as the two wizard routes.  Keep the block opt-in for truly old
    # requests that carry no lineage fields, while any explicit contract or
    # series selection is validated and materialized centrally.
    if (
        isinstance(cmd.selection_contract, dict)
        or cmd.series_id is not None
        or cmd.variant_id is not None
        or cmd.parent_model_version_id is not None
        or cmd.parent_training_job_id is not None
    ):
        task_dataset_id = getattr(task, "dataset_id", None)
        contract = (
            canonical_selection_contract(cmd.selection_contract)
            if isinstance(cmd.selection_contract, dict)
            else build_selection_contract(
                dataset_id=int(task_dataset_id) if task_dataset_id else None,
                mode="task",
                task_ids=[int(value) for value in source_ids],
                sample_conditions=cmd.sample_conditions,
                recipe={
                    "training_recipe_key": cmd.training_recipe_key,
                    "training_recipe_version": cmd.training_recipe_version,
                },
            )
        )
        parent_training_job_id = cmd.parent_training_job_id
        if parent_training_job_id is None and str(cmd.weights_source or "").lower() == "trained":
            parent_training_job_id = cmd.weights_ref_id
        lineage = model_series_service.resolve_model_lineage(
            db,
            current_user=current_user,
            selection_contract=contract,
            branch_key=cmd.branch_key,
            series_id=cmd.series_id,
            variant_id=cmd.variant_id,
            parent_model_version_id=cmd.parent_model_version_id,
            parent_training_job_id=parent_training_job_id,
            inheritance_mode=cmd.inheritance_mode,
            series_name=cmd.series_name,
            variant_name=cmd.variant_name,
            model_family=cmd.model_family or cmd.category or "yolo_detection",
        )
        db.commit()
        cmd = cmd.copy(
            update={
                "series_id": lineage.series_id,
                "variant_id": lineage.variant_id,
                "series_name": lineage.series.name,
                "variant_name": lineage.variant.name,
                "model_family": lineage.series.model_family,
                "parent_model_version_id": lineage.parent_model_version_id,
                "parent_training_job_id": lineage.parent_training_job_id,
                "branch_key": lineage.branch_key,
                "selection_contract": lineage.selection_contract,
                "inheritance_mode": lineage.inheritance_mode,
            }
        )

    if cmd.weights_source == "trained" and cmd.weights_ref_id:
        # The unified task-dimension route carries the frozen dataset contract
        # in hyperparams. Legacy callers may omit it, so the shared validator
        # falls back to the current task contract when one is available.
        parent_row = crud_yolo_train_job.get(
            db=db, job_id=int(cmd.weights_ref_id)
        )
        if parent_row is not None:
            from labelu.internal.application.service.training_iteration import (
                validate_explicit_iteration_parent,
            )

            validate_explicit_iteration_parent(
                db,
                current_user=current_user,
                parent_job_id=int(cmd.weights_ref_id),
                task_id=task_id,
                requested_recipe_key=cmd.training_recipe_key,
                requested_recipe_version=cmd.training_recipe_version,
                expected_label_set_signature=iteration_label_signature,
                expected_class_order_signature=iteration_order_signature,
            )

    if not ensure_ultralytics_installed(reason="创建训练任务"):
        raise LabelUException(
            code=ErrorCode.CODE_57003_YOLO_TRAIN_ULTRALYTICS_MISSING,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
        )

    _validate_weights_source_at_create(db, current_user, cmd)
    stored_weights = _model_weights_field_for_storage(cmd)

    execution_mode = "local"
    train_node_id = None

    hp_json = None
    if cmd.hyperparams:
        hp_json = json.dumps(normalize_hyperparams(cmd.hyperparams), ensure_ascii=False)
    selection_contract_text = None
    selection_contract_digest = None
    branch_identity_digest = None
    if isinstance(cmd.selection_contract, dict):
        normalized_contract = canonical_selection_contract(cmd.selection_contract)
        selection_contract_text = selection_contract_json(normalized_contract)
        selection_contract_digest = selection_contract_sha256(normalized_contract)
        branch_identity_digest = branch_identity_sha256(normalized_contract)
        if cmd.branch_key and str(cmd.branch_key).strip() not in {
            branch_identity_digest,
            selection_contract_digest,
        }:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="branch_key 与当前训练选择合同不一致，请刷新预览后重试",
            )

    replayed_job = None
    with begin_transaction(db):
        if not retry_key:
            job = crud_yolo_train_job.create(
                db=db,
                task_id=task_id,
                user_id=current_user.id,
                model_weights=stored_weights,
                epochs=cmd.epochs,
                imgsz=cmd.imgsz,
                category=(cmd.category or "").strip()[:128],
                hyperparams_json=hp_json,
                source_task_ids_json=source_task_ids_json(source_ids),
                training_recipe_key=(cmd.training_recipe_key or "").strip() or None,
                training_recipe_version=cmd.training_recipe_version,
                execution_mode=execution_mode,
                train_node_id=train_node_id,
                series_id=cmd.series_id,
                variant_id=cmd.variant_id,
                parent_model_version_id=cmd.parent_model_version_id,
                parent_training_job_id=cmd.parent_training_job_id,
                branch_key=(cmd.branch_key or branch_identity_digest),
                selection_contract_sha256=selection_contract_digest,
                selection_contract_json=selection_contract_text,
                sample_conditions_json=json.dumps(
                    cmd.sample_conditions or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                inheritance_mode=cmd.inheritance_mode,
            )
        else:
            try:
                with db.begin_nested():
                    job = crud_yolo_train_job.create(
                        db=db,
                        task_id=task_id,
                        user_id=current_user.id,
                        model_weights=stored_weights,
                        epochs=cmd.epochs,
                        imgsz=cmd.imgsz,
                        category=(cmd.category or "").strip()[:128],
                        hyperparams_json=hp_json,
                        source_task_ids_json=source_task_ids_json(source_ids),
                        training_recipe_key=(cmd.training_recipe_key or "").strip()
                        or None,
                        training_recipe_version=cmd.training_recipe_version,
                        execution_mode=execution_mode,
                        train_node_id=train_node_id,
                        idempotency_key=retry_key,
                        idempotency_request_hash=retry_hash,
                        series_id=cmd.series_id,
                        variant_id=cmd.variant_id,
                        parent_model_version_id=cmd.parent_model_version_id,
                        parent_training_job_id=cmd.parent_training_job_id,
                        branch_key=(cmd.branch_key or branch_identity_digest),
                        selection_contract_sha256=selection_contract_digest,
                        selection_contract_json=selection_contract_text,
                        sample_conditions_json=json.dumps(
                            cmd.sample_conditions or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        inheritance_mode=cmd.inheritance_mode,
                    )
            except IntegrityError:
                replayed_job = crud_yolo_train_job.get_by_idempotency(
                    db, creator_id=current_user.id, idempotency_key=retry_key
                )
                if replayed_job is None:
                    raise
                if replayed_job.idempotency_request_hash != retry_hash:
                    raise LabelUException(
                        code=ErrorCode.CODE_30003_CLIENT_ERROR,
                        status_code=status.HTTP_409_CONFLICT,
                        message="Idempotency-Key 已用于其他训练请求",
                    )
                job = replayed_job
        if replayed_job is not None:
            job_id = int(replayed_job.id)
        else:
            job_id = job.id
        if replayed_job is None:
            cmd = cmd.copy(
                update={
                    "model_weights": stored_weights,
                    "execution_mode": "local",
                    "train_node_id": None,
                }
            )

    if replayed_job is not None:
        return _job_to_response(
            db, replayed_job, task, include_training_task_detail=True
        )

    _job_work_root(job_id).mkdir(parents=True, exist_ok=True)
    _append_train_process_log(
        job_id,
        (
            f"===== YOLO training job_id={job_id} task_id={task_id} created "
            f"epochs={cmd.epochs} imgsz={cmd.imgsz} weights={stored_weights} "
            f"execution={execution_mode}"
            + (f" node={train_node_id}" if train_node_id else "")
            + " ====="
        ),
    )
    if execution_mode == "remote":
        _append_train_process_log(
            job_id, "phase=queued detail=waiting for remote dispatch"
        )
    else:
        _append_train_process_log(
            job_id, "phase=queued detail=waiting for GPU resources"
        )

    # Unified training persists its immutable DatasetVersion lineage in the
    # adapter layer immediately after this service returns.  Let that caller
    # defer queue submission until the lineage transaction has committed so a
    # worker can never observe the newly-created job without its manifest.
    if not defer_dispatch:
        _submit_yolo_train_job(job_id, task_id, cmd)

    resp = _job_to_response(db, job, task)
    if len(source_ids) > 1:
        resp.merge_task_ids = source_ids
        resp.merge_task_label = merge_task_label(source_ids)
        resp.merge_task_sample_counts = {str(k): v for k, v in per_task_counts.items()}
    return resp


def _submit_yolo_train_job(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool = False,
) -> None:
    """
    经资源队列派发：GPU/并发槽位不足时保持 PENDING 排队，有空位后再开训。
    在 FastAPI 协程内用 run_in_executor；在评估/看门狗等普通线程内用 daemon Thread。
    """
    if (cmd.execution_mode or "local") == "remote":
        from labelu.internal.application.service.remote_yolo_train import (
            request_remote_train_start,
        )

        request_remote_train_start(job_id, task_id, cmd, resume=resume)
        return

    from labelu.internal.application.service.yolo_train_queue import (
        dispatch_all_available_train_jobs,
        request_train_start,
    )

    request_train_start(job_id, task_id, cmd, resume=resume)
    dispatch_all_available_train_jobs()


def _run_yolo_train_sync(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    resume: bool = False,
    train_device: Optional[str] = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    from labelu.internal.common.db import session_scope

    from labelu.internal.common.pipeline_process_log import (
        ProcessLogWriter,
        append_related_files,
        tee_stdio,
        yolo_train_log_path,
    )

    _ACTIVE_TRAIN_JOBS.add(int(job_id))
    _ensure_train_sigterm_hooks()
    work_root = _job_work_root(job_id)
    dataset_root = work_root / "dataset"
    train_n = val_n = test_n = 0
    classes: List[str] = []
    source_ids: list[int] = []
    per_task_counts: dict[int, int] = {}
    per_task_exported: dict[int, int] = {}
    weights_path = ""
    log_path = yolo_train_log_path(job_id)
    train_log = ProcessLogWriter(
        log_path,
        logger_prefix=f"YOLO train job_id={job_id}",
        append_if_exists=True,
        header=(
            f"===== YOLO training worker started job_id={job_id} task_id={task_id} "
            f"resume={resume} pid={os.getpid()} ====="
        ),
    )

    try:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                return
            if not resume and (_job_is_cancelled(job) or is_stop_requested(job_id)):
                _mark_job_cancelled(
                    job_id,
                    db=db,
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
                return
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db=db, job_id=job_id)
                if not job or (not resume and _job_is_cancelled(job)):
                    return
                _assert_train_result_write_fence(
                    db,
                    job_id=job_id,
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    YoloTrainStatus.PROCESSING.value,
                    error_message=None,
                )

            work_root.mkdir(parents=True, exist_ok=True)
            if resume:
                _write_train_phase(
                    job_id,
                    "loading_model",
                    detail="resume from last.pt",
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
                last_path = work_root / "train" / "weights" / "last.pt"
                if not last_path.is_file():
                    raise RuntimeError("未找到 last.pt，无法继续运行")
                train_n = int(job.train_sample_count or 0)
                val_n = int(job.val_sample_count or 0)
                test_n = int(_test_sample_count_for_job(job) or 0)
                source_ids = parse_source_task_ids(job)
                per_task_counts = _merge_done_counts_from_job(job)
                per_task_exported = _merge_exported_counts_from_job(job)
                classes = _dataset_class_names_for_summary(job, dataset_root)
            else:
                _write_train_phase(
                    job_id,
                    "preparing_dataset",
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
                _raise_if_stop_requested(job_id)

                source_ids = parse_source_task_ids(job)
                owner = crud_user.get(db, job.created_by) if job.created_by else None
                if not owner:
                    raise RuntimeError("训练作业缺少创建者")
                forced_splits = None
                if getattr(job, "auto_training_round_id", None):
                    from labelu.internal.application.service.auto_training_dataset import (
                        group_frozen_samples_by_task,
                        load_frozen_sample_proxies,
                    )

                    samples, forced_splits = load_frozen_sample_proxies(
                        db, round_id=int(job.auto_training_round_id)
                    )
                    source_ids = list(
                        dict.fromkeys(int(sample.task_id) for sample in samples)
                    )
                    _task, task_config, samples, per_task_counts = (
                        load_merged_training_context(
                            db,
                            primary_task_id=task_id,
                            source_task_ids=source_ids,
                            current_user=owner,
                            frozen_samples_by_task=group_frozen_samples_by_task(
                                samples
                            ),
                        )
                    )
                elif getattr(job, "training_task_id", None):
                    task_config, samples, per_task_counts, source_ids = (
                        training_task_service.build_label_dimension_dataset_context(
                            db,
                            current_user=owner,
                            training_task_id=int(job.training_task_id),
                        )
                    )
                else:
                    _task, task_config, samples, per_task_counts = (
                        load_merged_training_context(
                            db,
                            primary_task_id=task_id,
                            source_task_ids=source_ids,
                            current_user=owner,
                        )
                    )
                    frozen_manifest_samples = (
                        _frozen_manifest_samples_for_job(job) or []
                    )
                    frozen_sample_ids = {
                        int(item["sample_id"]) for item in frozen_manifest_samples
                    }
                    if frozen_sample_ids:
                        samples = [
                            sample
                            for sample in samples
                            if int(sample.id) in frozen_sample_ids
                        ]
                        per_task_counts = {}
                        for sample in samples:
                            source_task_id = int(sample.task_id)
                            per_task_counts[source_task_id] = (
                                per_task_counts.get(source_task_id, 0) + 1
                            )
                        if not samples:
                            raise RuntimeError(
                                "冻结数据集版本未包含当前任务可用样本，禁止重新解析任务数据"
                            )
                        _validate_frozen_sample_content(
                            samples,
                            frozen_manifest_samples,
                        )
                        _write_train_phase(
                            job_id,
                            "frozen_dataset_selected",
                            detail=f"sample_count={len(samples)}",
                            execution_owner=execution_owner,
                            execution_fencing_token=execution_fencing_token,
                        )

                train_n, val_n, test_n, classes, per_task_exported = (
                    prepare_yolo_dataset(
                        task_config=task_config,
                        samples=samples,
                        media_root=Path(settings.MEDIA_ROOT),
                        dataset_root=dataset_root,
                        val_ratio=settings.YOLO_TRAIN_VAL_RATIO,
                        test_ratio=float(settings.YOLO_TRAIN_TEST_RATIO),
                        seed=settings.YOLO_TRAIN_SPLIT_SEED,
                        forced_splits=forced_splits,
                        selected_class_names=_selected_class_names_for_job(job),
                        preset_label_items=(
                            _preset_label_items_for_training_task(
                                db, int(job.training_task_id)
                            )
                            if getattr(job, "training_task_id", None)
                            else _preset_label_items_for_config(db, task_config)
                        ),
                    )
                )
                dataset_validation = {"ok": True, "skipped": True}
                if RUN_DATASET_VALIDATION_AFTER_EXPORT:
                    from labelu.internal.application.service.yolo_dataset import (
                        yolo_job_reports_dir,
                    )

                    dataset_validation = validate_exported_yolo_dataset(
                        dataset_root,
                        strict=DATASET_VALIDATION_STRICT,
                        write_reports=True,
                        reports_dir=yolo_job_reports_dir(dataset_root),
                    )
                export_summary = {}
                export_summary_path = (
                    dataset_root.resolve().parent / "reports" / "export_summary.json"
                )
                try:
                    export_summary = json.loads(
                        export_summary_path.read_text(encoding="utf-8")
                    )
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    export_summary = {}
                dataset_manifest = build_dataset_manifest(
                    dataset_root,
                    source_task_ids=source_ids,
                    split_counts={
                        "train": train_n,
                        "val": val_n,
                        "test": test_n,
                    },
                    class_names=classes,
                    per_task_counts=per_task_counts,
                    sample_composition=(
                        export_summary.get("sample_composition")
                        if isinstance(export_summary, dict)
                        else None
                    ),
                )
                data_yaml_path = dataset_root / "data.yaml"
                data_yaml_sha256 = hashlib.sha256(
                    data_yaml_path.read_bytes()
                ).hexdigest()
                frozen_class_names = (
                    json.loads(job.class_names_json or "[]")
                    if getattr(job, "class_names_json", None)
                    else []
                )
                if frozen_class_names and list(classes) != list(frozen_class_names):
                    raise RuntimeError(
                        f"data.yaml 类别顺序与冻结契约不一致: yaml={classes}, contract={frozen_class_names}"
                    )
                source_sample_manifest = (
                    json.loads(job.source_sample_manifest_json or "[]")
                    if getattr(job, "source_sample_manifest_json", None)
                    else []
                )
                if source_sample_manifest:
                    for item in source_sample_manifest:
                        sample_prefix = f"{int(item['source_sample_id'])}_"
                        item["split"] = next(
                            (
                                split
                                for split in ("train", "val", "test")
                                if any(
                                    path.name.startswith(sample_prefix)
                                    for path in (dataset_root / "images" / split).glob(
                                        f"{sample_prefix}*"
                                    )
                                )
                            ),
                            None,
                        )
                merge_note = ""
                if len(source_ids) > 1:
                    merge_note = " merge_tasks=" + "+".join(
                        f"{tid}:{per_task_exported.get(tid, 0)}" for tid in source_ids
                    )
                elif len(source_ids) == 1:
                    tid = source_ids[0]
                    merge_note = f" task={tid}:{per_task_exported.get(tid, 0)}"
                _write_train_phase(
                    job_id,
                    "dataset_ready",
                    detail=(
                        f"train={train_n} val={val_n} test={test_n} total={train_n + val_n + test_n}"
                        f" classes={len(classes)}{merge_note}"
                    ),
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                )
                try:
                    partial_summary = json.dumps(
                        {
                            "dataset": {
                                "train": train_n,
                                "val": val_n,
                                "test": test_n,
                                "validation_ok": dataset_validation.get("ok"),
                            },
                            "merge_tasks": _merge_tasks_summary_payload(
                                source_ids,
                                per_task_counts,
                                per_task_exported,
                            ),
                        },
                        ensure_ascii=False,
                    )
                except (TypeError, ValueError):
                    partial_summary = None
                with begin_transaction(db):
                    job = crud_yolo_train_job.get(db=db, job_id=job_id)
                    if job:
                        _assert_train_result_write_fence(
                            db,
                            job_id=job_id,
                            execution_owner=execution_owner,
                            execution_fencing_token=execution_fencing_token,
                        )
                        crud_yolo_train_job.update_status(
                            db,
                            job,
                            job.status,
                            train_sample_count=train_n,
                            val_sample_count=val_n,
                            metrics_summary=(
                                partial_summary[:8000]
                                if partial_summary
                                else job.metrics_summary
                            ),
                            dataset_manifest_json=json.dumps(
                                dataset_manifest,
                                ensure_ascii=False,
                            ),
                        )
                        job.data_yaml_sha256 = data_yaml_sha256
                        job.dataset_content_signature = dataset_manifest.get(
                            "dataset_content_signature"
                        )
                        if source_sample_manifest:
                            job.source_sample_manifest_json = json.dumps(
                                source_sample_manifest, ensure_ascii=False
                            )
                        db.add(job)
                _raise_if_stop_requested(job_id)

            owner_id = int(job.created_by or 0)
            weights_path = (
                str((work_root / "train" / "weights" / "last.pt").resolve())
                if resume
                else resolve_initial_weights_for_training(
                    db, owner_id, cmd.model_weights or "yolo11n.pt"
                )
            )

        target_epochs = int(cmd.epochs or 0)
        train_loop_complete = (
            resume
            and target_epochs > 0
            and _is_ultralytics_train_loop_complete(work_root, target_epochs)
        )
        train_results: Any = None

        pending_cmd = _consume_pending_runtime_hyperparams_for_current_run(
            job_id,
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
        if pending_cmd:
            cmd = pending_cmd
            target_epochs = int(cmd.epochs or 0)

        if train_loop_complete:
            last_ep = read_last_epoch_from_results_csv(work_root / "train")
            logger.info(
                "YOLO train job {} train loop already complete (epoch {} >= {}), finalize only",
                job_id,
                last_ep,
                target_epochs,
            )
            _write_train_phase(
                job_id,
                "finalizing",
                detail=f"已训满 {target_epochs} epoch，跳过 model.train，写入指标",
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
        else:
            prepare_cuda_env_for_yolo_train()
            logger.info("YOLO train job {} {}", job_id, _torch_cuda_summary())
            cuda_hint = yolo_train_cuda_mismatch_hint()
            if cuda_hint:
                logger.warning("YOLO train job {} {}", job_id, cuda_hint)
            os.environ.setdefault("YOLO_OFFLINE", "True")
            _disable_ultralytics_online_amp_check(train_log)

            from ultralytics import YOLO

            _write_train_phase(
                job_id,
                "loading_model",
                detail=weights_path,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            model = YOLO(weights_path)
            (
                on_train_start,
                on_epoch_start,
                on_fit_end,
                on_batch_end,
                on_fit_complete,
            ) = _make_train_process_callbacks(
                job_id,
                train_log,
                work_root=work_root,
                target_epochs=target_epochs,
                execution_guard=_train_execution_write_guard(
                    job_id,
                    execution_owner=execution_owner,
                    execution_fencing_token=execution_fencing_token,
                ),
            )
            model.add_callback("on_train_start", on_train_start)
            model.add_callback("on_train_epoch_start", on_epoch_start)
            model.add_callback("on_fit_epoch_end", on_fit_end)
            model.add_callback("on_train_batch_end", on_batch_end)
            model.add_callback("on_train_end", on_fit_complete)
            _raise_if_stop_requested(job_id)
            train_device = _resolve_train_device_for_job(job_id, override=train_device)
            hp_extra = normalize_hyperparams(cmd.hyperparams) or {}
            if resume:
                train_kw = _build_resume_train_kwargs(
                    last_path=Path(weights_path),
                    cmd=cmd,
                    train_device=train_device,
                    job_id=job_id,
                )
            else:
                train_kw = dict(
                    data=str(dataset_root / "data.yaml"),
                    epochs=cmd.epochs,
                    imgsz=cmd.imgsz,
                    project=str(work_root),
                    name="train",
                    exist_ok=True,
                    pretrained=True,
                    verbose=True,
                    patience=default_train_patience(cmd.epochs),
                    save=True,
                    plots=True,
                    device=train_device,
                    **_default_train_runtime_kwargs(
                        train_device=train_device,
                        imgsz=int(cmd.imgsz or 640),
                        batch=int(
                            (normalize_hyperparams(cmd.hyperparams) or {}).get("batch")
                            or 16
                        ),
                    ),
                )
                train_kw = _merge_train_hyperparams(
                    train_kw,
                    hp_extra,
                    train_device=train_device,
                    job_id=job_id,
                )

            train_device = _apply_large_imgsz_safe_train_profile(
                train_kw,
                hp_extra,
                train_device=train_device,
                job_id=job_id,
                train_log=train_log,
                train_sample_count=train_n,
            )
            _ensure_safe_final_train_batch(
                train_kw,
                train_sample_count=train_n,
                job_id=job_id,
                train_log=train_log,
            )
            _write_train_phase(
                job_id,
                "running",
                detail=(
                    "resume from last.pt"
                    if resume
                    else f"epochs={cmd.epochs} device={train_device} workers={train_kw.get('workers')} amp={train_kw.get('amp')}"
                ),
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            logger.info(
                "YOLO train job {} starting model.train device={} workers={} amp={}",
                job_id,
                train_device,
                train_kw.get("workers"),
                train_kw.get("amp"),
            )
            train_log.write(
                f"model.train kwargs={json.dumps(train_kw, ensure_ascii=False, default=str)}"
            )
            train_results = _model_train_with_retry(
                model,
                train_kw,
                train_log=train_log,
                job_id=job_id,
                work_root=work_root,
            )
            append_related_files(
                train_log,
                [
                    work_root / "train" / "results.csv",
                    work_root / "train" / "args.yaml",
                ],
            )

        _finalize_yolo_train_job_sync(
            job_id=job_id,
            work_root=work_root,
            dataset_root=dataset_root,
            cmd=cmd,
            train_n=train_n,
            val_n=val_n,
            test_n=test_n,
            classes=classes,
            source_ids=source_ids,
            per_task_counts=per_task_counts,
            per_task_exported=per_task_exported,
            results=train_results,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
    except TrainExecutionFenceLost as exc:
        logger.warning("YOLO train job {} result write fenced: {}", job_id, exc)
        _append_train_process_log(job_id, "result_write_fenced: " + str(exc))
        return
    except TrainStoppedByUser as e:
        logger.info("YOLO train job {} stopped by user", job_id)
        if _has_pending_runtime_hyperparams(job_id):
            _write_train_phase(
                job_id,
                "restarting",
                detail="应用临时超参并从 last.pt 续训",
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            if _apply_pending_runtime_hyperparams_and_restart(
                job_id,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            ):
                return
            logger.warning("runtime hyperparams restart failed job_id={}", job_id)
        _mark_job_cancelled(
            job_id,
            detail=str(e),
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
    except Exception as e:
        cancelled = is_stop_requested(job_id)
        if not cancelled:
            with session_scope() as db:
                cancelled = _job_is_cancelled(
                    crud_yolo_train_job.get(db=db, job_id=job_id)
                )
        if cancelled:
            _mark_job_cancelled(
                job_id,
                execution_owner=execution_owner,
                execution_fencing_token=execution_fencing_token,
            )
            _cleanup_train_runtime_after_terminal_failure(job_id)
            return
        fail_detail = _format_train_failure_detail(job_id, e)
        train_log.write_block("train_failure", fail_detail)
        _write_train_phase(
            job_id,
            "failed",
            detail=fail_detail[:500],
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
        logger.error("YOLO train job {} failed: {}", job_id, fail_detail)
        try:
            with session_scope() as db:
                job = crud_yolo_train_job.get(db=db, job_id=job_id)
                if job and not _job_is_cancelled(job):
                    with begin_transaction(db):
                        _assert_train_result_write_fence(
                            db,
                            job_id=job_id,
                            execution_owner=execution_owner,
                            execution_fencing_token=execution_fencing_token,
                        )
                        crud_yolo_train_job.update_status(
                            db,
                            job,
                            YoloTrainStatus.FAILED.value,
                            error_message=str(e),
                        )
        except Exception:
            logger.exception("failed to update yolo train job status")
        _cleanup_train_runtime_after_terminal_failure(job_id)
    finally:
        train_log.close()
        _ACTIVE_TRAIN_JOBS.discard(int(job_id))


def get_train_job(
    db: Session, task_id: int, job_id: int, current_user: User
) -> YoloTrainJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return _job_to_response(
        db,
        job,
        task,
        include_training_task_detail=True,
    )


def get_train_job_by_id(
    db: Session, job_id: int, current_user: User
) -> YoloTrainJobResponse:
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = crud_task.get(db=db, task_id=job.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    return _job_to_response(db, job, task, include_training_task_detail=True)


def _validation_names_map(yolo_model) -> dict[int, str]:
    names = getattr(yolo_model, "names", None)
    if isinstance(names, dict):
        return {int(k): str(v) for k, v in names.items()}
    if isinstance(names, (list, tuple)):
        return {idx: str(value) for idx, value in enumerate(names)}
    return {}


def _validation_scalar_xyxy(value) -> tuple[float, float, float, float]:
    data = value.tolist() if hasattr(value, "tolist") else value
    if data and isinstance(data[0], (list, tuple)):
        data = data[0]
    x1, y1, x2, y2 = [float(item) for item in data[:4]]
    return x1, y1, x2, y2


def _validation_scalar_float(value) -> float:
    if hasattr(value, "item"):
        return float(value.item())
    data = value.tolist() if hasattr(value, "tolist") else value
    if isinstance(data, list):
        data = data[0]
    return float(data)


def _plate_validation_artifacts(
    job: YoloTrainJobResponse,
) -> tuple[dict[str, Path], dict[str, Any]]:
    metadata = job.component_artifacts or {}
    pose_path = resolve_weights_path(job.weights_path)
    filenames = {
        "pose": "plate_pose_best.pt",
        "ocr": "plate_ocr_best.pt",
        "color": "plate_color_best.pt",
        "plate_type": "plate_type_best.pt",
    }
    resolved: dict[str, Path] = {"pose": pose_path}
    for component in ("ocr", "color", "plate_type"):
        component_meta = metadata.get(component) or {}
        stored = component_meta.get("pt") or str(
            pose_path.parent / filenames[component]
        )
        resolved[component] = resolve_weights_path(stored)
    return resolved, metadata


def _draw_validation_detections(
    image, detections: list[YoloModelValidationDetectionResponse]
) -> bytes:
    from PIL import ImageDraw

    colors = ("#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#ec4899")
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    for idx, det in enumerate(detections):
        color = colors[idx % len(colors)]
        draw.rectangle([det.x1, det.y1, det.x2, det.y2], outline=color, width=3)
        if len(det.corners) == 4:
            polygon = [(point.x, point.y) for point in det.corners]
            draw.line([*polygon, polygon[0]], fill="#f97316", width=3)
            for point in det.corners:
                radius = 5
                draw.ellipse(
                    [
                        point.x - radius,
                        point.y - radius,
                        point.x + radius,
                        point.y + radius,
                    ],
                    fill="#facc15",
                    outline="#111827",
                    width=1,
                )
        details = [f"{det.label} {det.confidence:.2f}"]
        if det.plate_number:
            details.append(
                f"No:{det.plate_number} {det.plate_number_confidence or 0:.2f}"
            )
        if det.color:
            details.append(f"Color:{det.color} {det.color_confidence or 0:.2f}")
        if det.plate_type:
            details.append(
                f"Type:{det.plate_type} {det.plate_type_confidence or 0:.2f}"
            )
        text_metrics = []
        for line in details:
            line_bbox = draw.textbbox((0, 0), line)
            text_metrics.append(
                (
                    line,
                    max(1, int(line_bbox[2] - line_bbox[0])),
                    max(1, int(line_bbox[3] - line_bbox[1])),
                )
            )
        panel_width = min(canvas.width, max(item[1] for item in text_metrics) + 8)
        panel_height = sum(item[2] + 4 for item in text_metrics) + 2
        text_x = max(0, min(int(det.x1), canvas.width - panel_width))
        text_y = max(0, int(det.y1) - panel_height - 4)
        draw.rectangle(
            [text_x, text_y, text_x + panel_width, text_y + panel_height],
            fill=color,
        )
        line_y = text_y + 3
        for line, _line_width, line_height in text_metrics:
            draw.text((text_x + 4, line_y), line, fill="#ffffff")
            line_y += line_height + 4
    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def validate_uploaded_image_for_job(
    *,
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
    upload_file: UploadFile,
    conf: float = 0.25,
    imgsz: int = 640,
) -> YoloModelValidateImageResponse:
    job = get_train_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    if (
        job.status != YoloTrainStatus.COMPLETED.value
        or not (job.weights_path or "").strip()
    ):
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    weights = resolve_weights_path(job.weights_path)

    raw = upload_file.file.read()
    if not raw:
        raise LabelUException(
            code=ErrorCode.CODE_51003_TASK_ATTACHMENT_INVALID_IMAGE,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    from PIL import Image, ImageOps

    try:
        image = Image.open(BytesIO(raw))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception:
        raise LabelUException(
            code=ErrorCode.CODE_51003_TASK_ATTACHMENT_INVALID_IMAGE,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not ensure_ultralytics_installed(reason="模型验证"):
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="未安装 ultralytics，无法执行模型验证",
        )

    detections: list[YoloModelValidationDetectionResponse] = []

    if job.job_type == "plate_recognition":
        from labelu.internal.application.service.plate_recognition_infer import (
            infer_plate_recognition,
        )

        artifacts, metadata = _plate_validation_artifacts(job)
        pose_classes = (metadata.get("pose") or {}).get("class_names") or ["doubleEP"]
        plate_results = infer_plate_recognition(
            image,
            artifacts=artifacts,
            metadata=metadata,
            confidence=float(conf),
            imgsz=int(imgsz),
        )
        for item in plate_results:
            detections.append(
                YoloModelValidationDetectionResponse(
                    label=str(pose_classes[0]),
                    **item,
                )
            )
    else:
        from ultralytics import YOLO

        model = YOLO(str(weights))
        results = model.predict(
            source=image, conf=float(conf), imgsz=int(imgsz), verbose=False
        )
        result = results[0]
        boxes = getattr(result, "boxes", None)
        keypoints = getattr(result, "keypoints", None)
        keypoint_xy = getattr(keypoints, "xy", None)
        keypoint_conf = getattr(keypoints, "conf", None)
        names_map = _validation_names_map(model)
        if boxes is not None and len(boxes):
            xyxy = boxes.xyxy
            cls_t = boxes.cls
            conf_t = boxes.conf
            for idx in range(len(boxes)):
                x1, y1, x2, y2 = _validation_scalar_xyxy(xyxy[idx])
                class_id = int(_validation_scalar_float(cls_t[idx]))
                confidence = round(_validation_scalar_float(conf_t[idx]), 4)
                corners = []
                if job.job_type == "plate_detection" and keypoint_xy is not None:
                    raw_points = keypoint_xy[idx]
                    raw_points = (
                        raw_points.tolist()
                        if hasattr(raw_points, "tolist")
                        else raw_points
                    )
                    raw_scores = []
                    if keypoint_conf is not None:
                        raw_scores = keypoint_conf[idx]
                        raw_scores = (
                            raw_scores.tolist()
                            if hasattr(raw_scores, "tolist")
                            else raw_scores
                        )
                    for point_index, point in enumerate(raw_points or []):
                        if len(point) < 2:
                            continue
                        corners.append(
                            {
                                "x": float(point[0]),
                                "y": float(point[1]),
                                "confidence": (
                                    float(raw_scores[point_index])
                                    if point_index < len(raw_scores or [])
                                    else None
                                ),
                            }
                        )
                detections.append(
                    YoloModelValidationDetectionResponse(
                        label=(names_map.get(class_id) or str(class_id)).strip()
                        or str(class_id),
                        confidence=confidence,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        corners=corners,
                    )
                )

    rendered = _draw_validation_detections(image, detections)
    result_data_url = "data:image/png;base64," + base64.standard_b64encode(
        rendered
    ).decode("ascii")
    max_confidence = max((item.confidence for item in detections), default=0.0)
    return YoloModelValidateImageResponse(
        task_id=int(task_id),
        job_id=int(job_id),
        job_type=job.job_type,
        model_source=(
            "4-component PT" if job.job_type == "plate_recognition" else weights.name
        ),
        task_name=job.task_name,
        image_width=int(image.width),
        image_height=int(image.height),
        box_count=len(detections),
        max_confidence=float(round(max_confidence, 4)),
        detections=detections,
        result_image_data_url=result_data_url,
    )


async def restart_train_job(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
) -> YoloTrainJobResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    old_job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not old_job or old_job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not _job_can_manual_restart(old_job):
        raise LabelUException(
            code=ErrorCode.CODE_57020_YOLO_TRAIN_JOB_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前训练作业不可继续运行",
        )
    if not ensure_ultralytics_installed(reason="继续运行训练任务"):
        raise LabelUException(
            code=ErrorCode.CODE_57003_YOLO_TRAIN_ULTRALYTICS_MISSING,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
        )

    if not _restart_train_job_from_existing_job(db, job_id):
        raise LabelUException(
            code=ErrorCode.CODE_57020_YOLO_TRAIN_JOB_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="无法继续运行训练作业",
        )
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    return _job_to_response(db, job, task)


def list_train_jobs_global(
    db: Session,
    current_user: User,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    category: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    status: Optional[str] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    page: int = 0,
    size: int = 20,
    project_id: Optional[int] = None,
) -> Tuple[List[YoloTrainJobListRowResponse], int]:
    """跨任务列出当前用户可见的图像标注任务下的训练作业。"""
    from labelu.internal.application.service import task as task_service

    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    unrestricted = is_task_superuser(current_user)
    builtin_admin = is_consistent_pool_admin(current_user)
    preset_slug = (rect_preset_slug or "").strip()
    task_ids_filter = None
    if preset_slug:
        task_ids_filter = task_service.list_image_task_ids_matching_rect_preset(
            db, current_user, preset_slug
        )
    rows, total = crud_yolo_train_job.list_jobs_for_user(
        db,
        current_user.id,
        task_name=task_name,
        search=search,
        category=category,
        dataset_category_id=dataset_category_id,
        task_ids=task_ids_filter,
        status=status,
        created_from=created_from,
        created_to=created_to,
        page=page,
        size=size,
        task_access_unrestricted=unrestricted,
        task_access_builtin_admin=builtin_admin,
        project_id=project_id,
    )
    creators = _creator_emails_by_user_ids(db, (job.created_by for job, _ in rows))
    data = []
    for job, task in rows:
        train_m = _training_val_metrics_for_job(job)
        mids = parse_source_task_ids(job)
        sc = _sample_counts_for_job(job)
        edge_validation = _latest_edge_validation_for_job(job)
        task_cfg = _parse_task_config(task.config)
        task_rect_preset_slug = (
            str(task_cfg.get("defaultRectPresetId") or "").strip() or None
        )
        merge_exported = _merge_exported_counts_from_job(job)
        merge_done = _merge_done_counts_from_job(job)
        merge_counts_display = merge_exported if merge_exported else merge_done
        runtime_fields = _runtime_fields_for_job(job)
        training_task_fields = _training_task_response_fields(db, job, task.name)
        plate_fields = _plate_summary_fields(job)
        composite_training_assessment = _composite_training_assessment_for_job(job)
        stop_info = training_stop_info(
            job_id=job.id,
            status=job.status,
            configured_epochs=job.epochs,
            hyperparams=_job_hyperparams_dict(job),
            weights_path=job.weights_path,
            metrics_summary=job.metrics_summary,
        )
        assessment_payload = build_assessment_payload(
            training_val_metrics=train_m,
            test_val_metrics=None,
            val_test_comparison=None,
            total_sample_count=sc.get("total"),
            edge_validation=edge_validation,
            rect_preset_slug=task_rect_preset_slug,
            quality_policy_snapshot=quality_policy_snapshot_for_job(job),
        )
        data.append(
            YoloTrainJobListRowResponse(
                id=job.id,
                job_type=str(getattr(job, "job_type", None) or "yolo_detection"),
                component_progress=plate_fields["component_progress"],
                component_metrics=plate_fields["component_metrics"],
                composite_assessment=plate_fields["composite_assessment"],
                strategy_task=_strategy_task_marker_for_train_job(db, int(job.id)),
                task_id=job.task_id,
                project_id=(
                    getattr(job, "project_id", None)
                    or getattr(task, "project_id", None)
                ),
                task_name=task.name,
                training_task_id=training_task_fields["training_task_id"],
                training_task_name=training_task_fields["training_task_name"],
                training_task_mode=training_task_fields["training_task_mode"],
                training_task_display_subtitle=training_task_fields[
                    "training_task_display_subtitle"
                ],
                dataset_category_id=training_task_fields["dataset_category_id"],
                dataset_category_key=training_task_fields["dataset_category_key"],
                dataset_category_name=training_task_fields["dataset_category_name"],
                **_job_contract_response_fields(job),
                merge_task_ids=mids if len(mids) > 1 else None,
                merge_task_label=merge_task_label(mids) if len(mids) > 1 else None,
                merge_task_sample_counts=(
                    {str(k): v for k, v in merge_counts_display.items()}
                    if merge_counts_display
                    else None
                ),
                category=getattr(job, "category", None) or "",
                status=job.status,
                hyperparams=_job_hyperparams_dict(job),
                model_weights=job.model_weights,
                epochs=job.epochs,
                actual_epochs=stop_info["actual_epochs"],
                train_stop=stop_info,
                imgsz=job.imgsz,
                train_sample_count=sc.get("train"),
                val_sample_count=sc.get("val"),
                test_sample_count=sc.get("test"),
                training_assessment=(
                    composite_training_assessment
                    or assessment_payload.training_assessment
                ),
                edge_validation_assessment=assessment_payload.edge_validation_assessment,
                release_gate_assessment=assessment_payload.release_gate_assessment,
                quality_assessment=assessment_payload.quality_assessment,
                validation_pass=_validation_pass_for_job(job),
                total_sample_count=sc.get("total"),
                runtime_status=runtime_fields.get("runtime_status"),
                runtime_pid=runtime_fields.get("runtime_pid"),
                is_restartable=bool(runtime_fields.get("is_restartable")),
                restart_reason=runtime_fields.get("restart_reason"),
                created_at=job.created_at,
                updated_at=job.updated_at,
                creator_email=(
                    creators.get(int(job.created_by)) if job.created_by else None
                ),
                **_remote_fields_for_job(job),
                **_local_gpu_fields_for_job(job),
            )
        )
    return data, total


def _task_ids_for_rect_preset_filter(
    db: Session,
    current_user: User,
    *,
    rect_preset_slug: Optional[str] = None,
) -> Optional[List[int]]:
    from labelu.internal.application.service import task as task_service

    preset_slug = (rect_preset_slug or "").strip()
    if not preset_slug:
        return None
    return task_service.list_image_task_ids_matching_rect_preset(
        db, current_user, preset_slug
    )


def _training_catalog_fields(
    assessment: Optional[TrainingAssessmentResponse],
) -> dict[str, str]:
    code = str(getattr(assessment, "status_code", None) or "").strip()
    if code == "passed":
        return {"training_status": "passed", "training_status_label": "达标"}
    if code == "failed":
        return {"training_status": "failed", "training_status_label": "不达标"}
    return {
        "training_status": "review_required",
        "training_status_label": "待复核",
    }


def _bm1688_compile_fields(export_row: Any) -> dict[str, Any]:
    if export_row is None:
        return {
            "bm1688_compile_status": "not_started",
            "bm1688_compile_status_label": "未编译",
        }
    raw_status = str(getattr(export_row, "status", None) or "").upper()
    bmodel_path = str(getattr(export_row, "bmodel_rel_path", None) or "").strip()
    if raw_status == "PENDING":
        compile_status, label = "queued", "排队中"
    elif raw_status in {"PROCESSING", "RUNNING"}:
        compile_status, label = "compiling", "编译中"
    elif raw_status == "COMPLETED" and bmodel_path:
        compile_status, label = "completed", "已编译"
    elif raw_status == "FAILED":
        compile_status, label = "failed", "编译失败"
    else:
        compile_status, label = "not_started", "未编译"
    return {
        "bm1688_export_id": int(export_row.id),
        "bm1688_export_status": raw_status or None,
        "bm1688_compile_status": compile_status,
        "bm1688_compile_status_label": label,
        "bm1688_compile_updated_at": getattr(export_row, "updated_at", None),
        "bm1688_bmodel_rel_path": bmodel_path or None,
    }


def list_trained_models_global(
    db: Session,
    current_user: User,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> Tuple[List[YoloTrainedModelRowResponse], int]:
    """已完成训练且有权重的作业列表（每个作业对应一个可下载的 best.pt）。"""
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    unrestricted = is_task_superuser(current_user)
    builtin_admin = is_consistent_pool_admin(current_user)
    task_ids_filter = _task_ids_for_rect_preset_filter(
        db, current_user, rect_preset_slug=rect_preset_slug
    )
    rows, total = crud_yolo_train_job.list_completed_models_for_user(
        db,
        current_user.id,
        task_name=task_name,
        search=search,
        dataset_category_id=dataset_category_id,
        task_ids=task_ids_filter,
        page=page,
        size=size,
        task_access_unrestricted=unrestricted,
        task_access_builtin_admin=builtin_admin,
    )
    return _build_trained_model_responses(db, current_user, rows), total


def _build_trained_model_responses(
    db: Session,
    current_user: User,
    rows,
    *,
    policy_snapshots: Optional[dict[str, dict[str, Any]]] = None,
) -> List[YoloTrainedModelRowResponse]:
    creators = _creator_emails_by_user_ids(db, (job.created_by for job, _ in rows))
    latest_exports = crud_yolo_bm1688_export.latest_by_job_ids(
        db, yolo_train_job_ids=(job.id for job, _ in rows)
    )
    from labelu.internal.application.service import (
        yolo_aicm_publish as publish_service,
    )

    edge_validations = publish_service.edge_validation_statuses_for_exports(
        db, list(latest_exports.values())
    )
    data: List[YoloTrainedModelRowResponse] = []
    for job, task in rows:
        train_m = _training_val_metrics_for_job(job)
        mf = _metrics_fields_for_job(job)
        sc = _sample_counts_for_job(job)
        latest_export = latest_exports.get(int(job.id))
        edge_validation = (
            edge_validations.get(int(latest_export.id))
            if latest_export is not None
            else None
        )
        task_cfg = _parse_task_config(task.config)
        task_rect_classes = build_yolo_rect_class_names(task_cfg)
        task_rect_preset_slug = (
            str(task_cfg.get("defaultRectPresetId") or "").strip() or None
        )
        assessment_payload = build_assessment_payload(
            training_val_metrics=train_m,
            test_val_metrics=mf["test_val_metrics"],
            val_test_comparison=mf["val_test_comparison"],
            total_sample_count=sc.get("total"),
            edge_validation=edge_validation,
            rect_preset_slug=task_rect_preset_slug,
            quality_policy_snapshot=(
                quality_policy_snapshot_for_job(job)
                or (policy_snapshots or {}).get(task_rect_preset_slug or "")
            ),
        )
        plate_fields = _plate_summary_fields(job)
        composite_training_assessment = _composite_training_assessment_for_job(job)
        training_task_fields = _training_task_response_fields(db, job, task.name)
        training_assessment = (
            composite_training_assessment or assessment_payload.training_assessment
        )
        data.append(
            YoloTrainedModelRowResponse(
                job_id=job.id,
                job_type=str(getattr(job, "job_type", None) or "yolo_detection"),
                component_progress=plate_fields["component_progress"],
                component_metrics=plate_fields["component_metrics"],
                composite_assessment=plate_fields["composite_assessment"],
                strategy_task=_strategy_task_marker_for_train_job(db, int(job.id)),
                task_id=job.task_id,
                task_name=task.name,
                training_task_id=training_task_fields["training_task_id"],
                training_task_name=training_task_fields["training_task_name"],
                training_task_mode=training_task_fields["training_task_mode"],
                training_task_display_subtitle=training_task_fields[
                    "training_task_display_subtitle"
                ],
                dataset_category_id=training_task_fields["dataset_category_id"],
                dataset_category_key=training_task_fields["dataset_category_key"],
                dataset_category_name=training_task_fields["dataset_category_name"],
                **_job_contract_response_fields(job),
                category=getattr(job, "category", None) or "",
                weights_filename=_weights_basename(job.weights_path),
                model_weights=job.model_weights,
                epochs=job.epochs,
                imgsz=job.imgsz,
                train_sample_count=sc.get("train"),
                val_sample_count=sc.get("val"),
                test_sample_count=sc.get("test"),
                total_sample_count=sc.get("total"),
                created_at=job.created_at,
                updated_at=job.updated_at,
                creator_email=(
                    creators.get(int(job.created_by)) if job.created_by else None
                ),
                training_val_metrics=train_m,
                test_val_metrics=mf["test_val_metrics"],
                val_test_comparison=mf["val_test_comparison"],
                training_assessment=training_assessment,
                edge_validation_assessment=assessment_payload.edge_validation_assessment,
                release_gate_assessment=assessment_payload.release_gate_assessment,
                quality_assessment=assessment_payload.quality_assessment,
                **_training_catalog_fields(training_assessment),
                **_bm1688_compile_fields(latest_export),
                validation_pass=_validation_pass_for_job(job),
                ai_eval_metrics=[],
                class_metrics=_class_metrics_for_job(job),
                task_rect_preset_slug=task_rect_preset_slug,
                task_rect_classes=task_rect_classes or None,
            )
        )
    return data


def _collect_trained_models_global(
    db: Session,
    current_user: User,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
) -> List[YoloTrainedModelRowResponse]:
    """读取完整模型目录，供全库门禁统计与跨页排序使用。"""
    rows: List[YoloTrainedModelRowResponse] = []
    page = 0
    while True:
        batch, total = list_trained_models_global(
            db,
            current_user,
            task_name=task_name,
            search=search,
            dataset_category_id=dataset_category_id,
            rect_preset_slug=rect_preset_slug,
            page=page,
            size=100,
        )
        rows.extend(batch)
        if len(rows) >= total or not batch:
            return rows
        page += 1


def _collect_trained_model_sources_global(
    db: Session,
    current_user: User,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
):
    """Load lightweight ORM rows; response enrichment happens after pagination."""
    unrestricted = is_task_superuser(current_user)
    builtin_admin = is_consistent_pool_admin(current_user)
    task_ids_filter = _task_ids_for_rect_preset_filter(
        db, current_user, rect_preset_slug=rect_preset_slug
    )
    rows = []
    page = 0
    while True:
        batch, total = crud_yolo_train_job.list_completed_models_for_user(
            db,
            current_user.id,
            task_name=task_name,
            search=search,
            dataset_category_id=dataset_category_id,
            task_ids=task_ids_filter,
            page=page,
            size=100,
            task_access_unrestricted=unrestricted,
            task_access_builtin_admin=builtin_admin,
        )
        rows.extend(batch)
        if len(rows) >= total or not batch:
            return rows
        page += 1


def _quality_policy_snapshots_for_tasks(rows) -> dict[str, dict[str, Any]]:
    from labelu.internal.application.service.yolo_quality_policy import (
        resolve_policy_for_preset,
    )

    snapshots: dict[str, dict[str, Any]] = {}
    for _, task in rows:
        task_cfg = _parse_task_config(task.config)
        slug = str(task_cfg.get("defaultRectPresetId") or "").strip()
        if slug not in snapshots:
            snapshots[slug] = resolve_policy_for_preset(slug or None) or {
                "_catalog_default": True
            }
    return snapshots


def _trained_model_source_entry(
    job: YoloTrainJob,
    task: Task,
    *,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    training_val_metrics = _training_val_metrics_for_job(job)
    metrics_fields = _metrics_fields_for_job(job)
    sample_counts = _sample_counts_for_job(job)
    task_cfg = _parse_task_config(task.config)
    task_rect_preset_slug = (
        str(task_cfg.get("defaultRectPresetId") or "").strip() or None
    )
    assessment_payload = build_assessment_payload(
        training_val_metrics=training_val_metrics,
        test_val_metrics=metrics_fields["test_val_metrics"],
        val_test_comparison=metrics_fields["val_test_comparison"],
        total_sample_count=sample_counts.get("total"),
        edge_validation=None,
        rect_preset_slug=task_rect_preset_slug,
        quality_policy_snapshot=(
            quality_policy_snapshot or quality_policy_snapshot_for_job(job)
        ),
    )
    training_assessment = (
        _composite_training_assessment_for_job(job)
        or assessment_payload.training_assessment
    )
    status_code = _training_catalog_fields(training_assessment)["training_status"]
    test_metrics = metrics_fields["test_val_metrics"]
    test_map50_95 = getattr(test_metrics, "map50_95", None)
    if test_map50_95 is None:
        test_map50_95 = getattr(training_val_metrics, "map50_95", None)
    comparison = metrics_fields["val_test_comparison"]
    generalization_delta = getattr(comparison, "delta_map50_95", None)
    return {
        "job": job,
        "task": task,
        "training_status": status_code,
        "test_map50_95": float(test_map50_95) if test_map50_95 is not None else -1.0,
        "generalization_delta": (
            float(generalization_delta)
            if generalization_delta is not None
            else float("inf")
        ),
        "sample_count": int(sample_counts.get("total") or 0),
        "updated_timestamp": job.updated_at.timestamp() if job.updated_at else 0.0,
    }


def _trained_model_training_status(row: YoloTrainedModelRowResponse) -> str:
    code = str(getattr(row, "training_status", None) or "").strip()
    if code in {"passed", "review_required", "failed"}:
        return code
    assessment_code = str(
        getattr(row.training_assessment, "status_code", None) or ""
    ).strip()
    if assessment_code == "passed":
        return "passed"
    if assessment_code == "failed":
        return "failed"
    return "review_required"


def _trained_model_test_map50_95(row: YoloTrainedModelRowResponse) -> float:
    test_value = getattr(row.test_val_metrics, "map50_95", None)
    if test_value is not None:
        return float(test_value)
    val_value = getattr(row.training_val_metrics, "map50_95", None)
    return float(val_value) if val_value is not None else -1.0


def _trained_model_generalization_delta(row: YoloTrainedModelRowResponse) -> float:
    value = getattr(row.val_test_comparison, "delta_map50_95", None)
    return float(value) if value is not None else float("inf")


def _trained_model_updated_timestamp(row: YoloTrainedModelRowResponse) -> float:
    return row.updated_at.timestamp() if row.updated_at is not None else 0.0


def _trained_model_catalog_summary(
    rows: List[YoloTrainedModelRowResponse],
) -> YoloTrainedModelCatalogSummary:
    counts = {"passed": 0, "review_required": 0, "failed": 0}
    for row in rows:
        counts[_trained_model_training_status(row)] += 1
    return YoloTrainedModelCatalogSummary(
        total=len(rows),
        training_passed=counts["passed"],
        training_review_required=counts["review_required"],
        training_failed=counts["failed"],
        publishable=counts["passed"],
        review_required=counts["review_required"],
        blocked=counts["failed"],
    )


def list_trained_model_catalog(
    db: Session,
    current_user: User,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    training_status: Optional[str] = None,
    quality_status: Optional[str] = None,
    sort_by: str = "training_priority",
    page: int = 0,
    size: int = 20,
) -> Tuple[
    List[YoloTrainedModelRowResponse],
    int,
    YoloTrainedModelCatalogSummary,
]:
    """模型资产目录：全量汇总后按训练门禁筛选、排序并分页。"""
    page = max(int(page), 0)
    size = min(max(int(size), 1), 100)
    source_rows = _collect_trained_model_sources_global(
        db,
        current_user,
        task_name=task_name,
        search=search,
        dataset_category_id=dataset_category_id,
        rect_preset_slug=rect_preset_slug,
    )
    policy_snapshots = _quality_policy_snapshots_for_tasks(source_rows)
    entries = [
        _trained_model_source_entry(
            job,
            task,
            quality_policy_snapshot=policy_snapshots.get(
                str(
                    _parse_task_config(task.config).get("defaultRectPresetId") or ""
                ).strip()
            ),
        )
        for job, task in source_rows
    ]
    counts = {"passed": 0, "review_required": 0, "failed": 0}
    for entry in entries:
        counts[entry["training_status"]] += 1
    summary = YoloTrainedModelCatalogSummary(
        total=len(entries),
        training_passed=counts["passed"],
        training_review_required=counts["review_required"],
        training_failed=counts["failed"],
        publishable=counts["passed"],
        review_required=counts["review_required"],
        blocked=counts["failed"],
    )
    normalized_status = str(training_status or "").strip()
    if not normalized_status:
        normalized_status = {
            "publishable": "passed",
            "review_required": "review_required",
            "blocked": "failed",
        }.get(str(quality_status or "").strip(), "")
    if normalized_status:
        entries = [
            entry for entry in entries if entry["training_status"] == normalized_status
        ]

    training_rank = {"passed": 0, "review_required": 1, "failed": 2}
    if sort_by == "latest":
        entries.sort(
            key=lambda entry: (
                entry["updated_timestamp"],
                int(entry["job"].id),
            ),
            reverse=True,
        )
    elif sort_by == "test_map50_95":
        entries.sort(
            key=lambda entry: (
                entry["test_map50_95"],
                entry["updated_timestamp"],
                int(entry["job"].id),
            ),
            reverse=True,
        )
    elif sort_by == "generalization_risk":
        entries.sort(
            key=lambda entry: (
                entry["generalization_delta"],
                -entry["updated_timestamp"],
                -int(entry["job"].id),
            )
        )
    elif sort_by == "sample_count":
        entries.sort(
            key=lambda entry: (
                entry["sample_count"],
                entry["updated_timestamp"],
                int(entry["job"].id),
            ),
            reverse=True,
        )
    else:
        entries.sort(
            key=lambda entry: (
                training_rank[entry["training_status"]],
                -entry["updated_timestamp"],
                -int(entry["job"].id),
            )
        )

    total = len(entries)
    start = page * size
    selected_rows = [
        (entry["job"], entry["task"]) for entry in entries[start : start + size]
    ]
    return (
        _build_trained_model_responses(
            db,
            current_user,
            selected_rows,
            policy_snapshots=policy_snapshots,
        ),
        total,
        summary,
    )


def _test_val_data_yaml_path(job_id: int) -> Path:
    """Resolve the dataset contract used by both standard and plate jobs."""
    work_root = _job_work_root(job_id)
    candidates = (
        work_root / "dataset" / "data.yaml",
        work_root / "plate_dataset" / "pose" / "data.yaml",
        work_root / "plate_dataset" / "data.yaml",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _run_test_val_sync(job_id: int, task_id: int) -> None:
    from labelu.internal.common.db import session_scope

    evaluation_operation_id = uuid.uuid4().hex
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job or job.task_id != task_id:
            return
        if job.status != YoloTrainStatus.COMPLETED.value:
            raise RuntimeError("仅已完成训练的作业可运行测试集验证")
        weights = resolve_weights_path(job.weights_path)
        if not weights or not weights.is_file():
            raise RuntimeError("未找到 best.pt 权重文件")
        data_yaml = _test_val_data_yaml_path(job_id)
        imgsz = int(job.imgsz or 640)
        prior_summary = job.metrics_summary

    device = _resolve_yolo_train_device()
    merged = prior_summary
    val_detail = run_split_val(
        weights_path=weights,
        data_yaml=data_yaml,
        split="val",
        imgsz=imgsz,
        device=device,
    )
    merged = merge_val_per_class_into_metrics_summary(merged, val_detail)

    test_val = run_split_val(
        weights_path=weights,
        data_yaml=data_yaml,
        split="test",
        imgsz=imgsz,
        device=device,
    )
    merged = merge_test_val_into_metrics_summary(merged, test_val)
    try:
        upgraded_summary = json.loads(merged or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        upgraded_summary = {}
    if not isinstance(upgraded_summary, dict):
        upgraded_summary = {}
    if isinstance(upgraded_summary, dict):
        train_dir = weights.parent.parent
        last_epoch_metrics = parse_results_csv_final_metrics(train_dir)
        best_epoch_metrics = parse_results_csv_best_metrics(train_dir)
        if last_epoch_metrics:
            upgraded_summary["last_epoch_metrics"] = last_epoch_metrics
            upgraded_summary.setdefault("final_metrics", last_epoch_metrics)
        if best_epoch_metrics:
            upgraded_summary["best_epoch_metrics"] = best_epoch_metrics
            best_pt_metrics = upgraded_summary.get("best_pt_val_metrics")
            if isinstance(best_pt_metrics, dict):
                best_pt_metrics["selected_epoch"] = best_epoch_metrics.get("epoch")
        upgraded_summary["metrics_contract_version"] = 2
        upgraded_summary["test_val_evaluation_operation_id"] = evaluation_operation_id
        merged = json.dumps(upgraded_summary, ensure_ascii=False, default=str)
    with session_scope() as db:
        with begin_transaction(db):
            applied = crud_yolo_train_job.update_metrics_summary_if_current(
                db,
                job_id=job_id,
                expected_metrics_summary=prior_summary,
                metrics_summary=merged,
            )
            if not applied:
                logger.warning(
                    "discard stale test-val result job_id={} operation_id={}",
                    job_id,
                    evaluation_operation_id,
                )


async def run_test_val_for_job(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
) -> YoloTrainJobResponse:
    """对已完成作业在 test 划分上运行 model.val（可重复执行以刷新指标）。"""
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job.status != YoloTrainStatus.COMPLETED.value:
        raise LabelUException(
            code=ErrorCode.CODE_57001_YOLO_TRAIN_TASK_NOT_FINISHED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练未完成，无法运行测试集验证",
        )
    ensure_ultralytics_installed()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_test_val_sync, job_id, task_id)
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    return _job_to_response(db, job, task)


def _weights_upload_suffix_ok(filename: str) -> bool:
    name = (filename or "").lower().strip()
    return name.endswith(".pt") or name.endswith(".pth")


def _imported_to_response(
    row: YoloImportedModel,
    *,
    creator_email: Optional[str] = None,
    can_delete: bool = False,
    registered_model_count: int = 0,
    registered_display_name: Optional[str] = None,
) -> YoloImportedModelRowResponse:
    return YoloImportedModelRowResponse(
        id=row.id,
        display_name=row.display_name or "",
        registered_display_name=(registered_display_name or "").strip() or None,
        category=getattr(row, "category", None) or "",
        original_filename=row.original_filename or "",
        file_size=int(row.file_size) if row.file_size is not None else None,
        created_at=row.created_at,
        creator_email=creator_email,
        can_delete=can_delete,
        registered_model_count=max(0, int(registered_model_count)),
        training_val_metrics=None,
        validation_pass=YoloValidationPassAssessment(
            status="无验证指标",
            detail="外部导入权重未在本平台训练，无验证集 mAP 可对齐阈值",
        ),
        ai_eval_metrics=[],
    )


def _registration_import_ids(*raw_values: Any) -> set[int]:
    """Read the source upload id persisted on an external registration.

    New registrations copy the id into both the artifact metadata and model
    manifest.  Keeping this parser tolerant lets legacy rows continue to use
    the path association when those audit fields are absent.
    """
    imported_ids: set[int] = set()
    for raw in raw_values:
        if isinstance(raw, dict):
            payload = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
        else:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            imported_id = int(payload.get("imported_model_id"))
        except (TypeError, ValueError):
            continue
        if imported_id > 0:
            imported_ids.add(imported_id)
    return imported_ids


async def create_imported_weights_model(
    db: Session,
    current_user: User,
    file: UploadFile,
    display_name: Optional[str],
    category: Optional[str],
) -> YoloImportedModelRowResponse:
    orig_name = (file.filename or "weights.pt").strip() or "weights.pt"
    if not _weights_upload_suffix_ok(orig_name):
        raise LabelUException(
            code=ErrorCode.CODE_57012_YOLO_IMPORT_INVALID_FILE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="仅支持上传扩展名为 .pt 或 .pth 的权重文件",
        )

    subdir = (
        Path(settings.YOLO_TRAIN_WORK_SUBDIR) / "imported" / f"user-{current_user.id}"
    )
    dest_dir = (Path(settings.MEDIA_ROOT) / subdir).resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    if not str(dest_dir).startswith(str(root)):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_file = f"{uuid.uuid4().hex}.pt"
    full_path = dest_dir / safe_file
    max_sz = int(settings.UPLOAD_FILE_MAX_SIZE)
    total = 0
    try:
        with full_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_sz:
                    raise LabelUException(
                        code=ErrorCode.CODE_57012_YOLO_IMPORT_INVALID_FILE,
                        status_code=status.HTTP_400_BAD_REQUEST,
                        message=f"文件过大，单文件上限为 {max_sz} 字节",
                    )
                out.write(chunk)
    except LabelUException:
        if full_path.is_file():
            try:
                full_path.unlink()
            except OSError:
                pass
        raise
    except Exception as exc:
        if full_path.is_file():
            try:
                full_path.unlink()
            except OSError:
                pass
        logger.exception("save imported yolo weights failed: {}", exc)
        raise LabelUException(
            code=ErrorCode.CODE_51000_CREATE_ATTACHMENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    rel = full_path.relative_to(root)
    stored = rel.as_posix()
    dn = (display_name or "").strip() or Path(orig_name).stem
    dn = dn[:128]
    cat = (category or "").strip()[:128]

    try:
        with begin_transaction(db):
            row = crud_yolo_imported_model.create(
                db,
                user_id=current_user.id,
                display_name=dn,
                category=cat,
                original_filename=orig_name[:256],
                weights_rel_path=stored[:512],
                file_size=total,
            )
    except Exception:
        try:
            full_path.unlink()
        except OSError:
            pass
        raise

    return _imported_to_response(
        row,
        creator_email=(current_user.username or "").strip() or None,
        can_delete=True,
    )


def list_imported_models_global(
    db: Session,
    current_user: User,
    *,
    search: Optional[str] = None,
    name: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> Tuple[List[YoloImportedModelRowResponse], int]:
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    unrestricted = is_task_superuser(current_user)
    rows, total = crud_yolo_imported_model.list_for_user(
        db,
        current_user.id,
        search=search,
        name=name,
        category=category,
        page=page,
        size=size,
        list_all_users_models=unrestricted,
        public_owner_ids=(
            [] if unrestricted else crud_yolo_imported_model.public_owner_ids(db)
        ),
    )
    creators = _creator_emails_by_user_ids(db, (r.user_id for r in rows))
    paths = [str(r.weights_rel_path or "") for r in rows if r.weights_rel_path]
    registered_counts: dict[int, int] = {}
    registered_display_names: dict[int, str] = {}
    if paths:
        registration_query = (
            db.query(
                ModelArtifact.relative_path,
                ModelArtifact.metadata_json,
                ModelVersion.manifest_json,
                ModelVersion.display_name,
                ModelVersion.id,
            )
            .join(
                ModelVersion,
                ModelVersion.id == ModelArtifact.model_version_id,
            )
            .filter(
                ModelArtifact.relative_path.in_(paths),
                ModelArtifact.artifact_type == "pt",
                func.upper(func.coalesce(ModelArtifact.status, "")) == "READY",
                ModelVersion.training_job_id.is_(None),
            )
        )
        if not unrestricted:
            registration_query = registration_query.filter(
                or_(
                    ModelVersion.created_by == int(current_user.id),
                    ModelVersion.status == "PUBLISHED",
                )
            )
        registration_rows = registration_query.order_by(
            ModelArtifact.relative_path.asc(),
            ModelVersion.created_at.desc(),
            ModelVersion.id.desc(),
        ).all()
        registrations_by_path: dict[str, list[tuple[set[int], str, int]]] = {}
        for (
            relative_path,
            metadata_json,
            manifest_json,
            display_name,
            model_id,
        ) in registration_rows:
            path_key = str(relative_path or "")
            registrations_by_path.setdefault(path_key, []).append(
                (
                    _registration_import_ids(metadata_json, manifest_json),
                    str(display_name or "").strip(),
                    int(model_id),
                )
            )
        for imported_row in rows:
            imported_id = int(imported_row.id)
            path_key = str(imported_row.weights_rel_path or "")
            candidates = registrations_by_path.get(path_key, [])
            exact = [
                candidate
                for candidate in candidates
                if imported_id in candidate[0]
            ]
            legacy = [candidate for candidate in candidates if not candidate[0]]
            selected = exact or legacy
            model_ids: set[int] = set()
            for _source_ids, display_name, model_id in selected:
                model_ids.add(model_id)
                if display_name and imported_id not in registered_display_names:
                    registered_display_names[imported_id] = display_name
            registered_counts[imported_id] = len(model_ids)
    return [
        _imported_to_response(
            r,
            creator_email=creators.get(int(r.user_id)) if r.user_id else None,
            can_delete=unrestricted or int(r.user_id) == int(current_user.id),
            registered_model_count=registered_counts.get(int(r.id), 0),
            registered_display_name=registered_display_names.get(int(r.id)),
        )
        for r in rows
    ], total


def get_imported_weights_download(
    db: Session, current_user: User, model_id: int
) -> Tuple[Path, str]:
    if is_task_superuser(current_user):
        row = crud_yolo_imported_model.get_by_id(db, model_id)
    else:
        row = crud_yolo_imported_model.get_available_for_user(
            db,
            model_id=model_id,
            user_id=current_user.id,
            public_owner_ids=crud_yolo_imported_model.public_owner_ids(db),
        )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    path = resolve_weights_path(row.weights_rel_path)
    fn = (
        row.original_filename or f"imported-{model_id}.pt"
    ).strip() or f"imported-{model_id}.pt"
    return path, fn


def delete_imported_weights_model(
    db: Session, current_user: User, model_id: int
) -> dict[str, Any]:
    if is_task_superuser(current_user):
        row = crud_yolo_imported_model.get_by_id(db, model_id)
    else:
        row = crud_yolo_imported_model.get_for_user(
            db, model_id=model_id, user_id=current_user.id
        )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    stored = row.weights_rel_path or ""
    registered_model_count = (
        db.query(func.count(func.distinct(ModelArtifact.model_version_id)))
        .filter(ModelArtifact.relative_path == stored)
        .scalar()
        or 0
    )

    with begin_transaction(db):
        crud_yolo_imported_model.delete_row(db, row)

    file_deleted = int(registered_model_count) == 0
    if file_deleted:
        _unlink_under_media(stored)
    return {
        "deleted": True,
        "file_deleted": file_deleted,
        "retained_for_registered_models": int(registered_model_count) > 0,
        "registered_model_count": int(registered_model_count),
    }


def _media_root_resolved() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def _safe_path_under_media(path: Path) -> Optional[Path]:
    root = _media_root_resolved()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if str(resolved).startswith(str(root)):
        return resolved
    return None


def _unlink_under_media(rel_or_abs: str | None) -> None:
    if not rel_or_abs or not str(rel_or_abs).strip():
        return
    p = Path(str(rel_or_abs).strip())
    if not p.is_absolute():
        p = _media_root_resolved() / p
    safe = _safe_path_under_media(p)
    if safe and safe.is_file():
        try:
            safe.unlink()
        except OSError as exc:
            logger.warning("failed to unlink {}: {}", safe, exc)


def _rmtree_under_media(path: Path) -> None:
    safe = _safe_path_under_media(path)
    if safe and safe.is_dir():
        try:
            shutil.rmtree(safe, ignore_errors=True)
        except OSError as exc:
            logger.warning("failed to rmtree {}: {}", safe, exc)


def _purge_board_validation_disk_for_exports(export_snapshots: list[dict]) -> None:
    """删除各 BM1688 导出下的板端验证打包产物（与 board_agent.purge 一致）。"""
    from labelu.internal.application.service.board_eval_pack import (
        clear_board_eval_pack_for_export,
    )

    for exp in export_snapshots:
        exp_id = exp.get("id")
        if exp_id is None:
            continue
        try:
            clear_board_eval_pack_for_export(int(exp_id))
        except OSError as exc:
            logger.warning("clear board eval pack export_id={}: {}", exp_id, exc)


def _purge_train_job_disk(
    job_id: int,
    *,
    weights_path: str | None,
    export_snapshots: list[dict],
) -> None:
    _purge_board_validation_disk_for_exports(export_snapshots)
    _rmtree_under_media(_job_work_root(job_id))
    _unlink_under_media(weights_path)
    root = _media_root_resolved()
    sub = settings.YOLO_BM1688_EXPORT_SUBDIR
    for exp in export_snapshots:
        exp_id = exp.get("id")
        if exp_id is not None:
            _rmtree_under_media(root / sub / str(exp_id))
        _unlink_under_media(exp.get("onnx_rel_path"))
        _unlink_under_media(exp.get("bmodel_rel_path"))


def pause_train_job(
    db: Session, task_id: int, job_id: int, current_user: User
) -> YoloTrainJobResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job.status not in (
        YoloTrainStatus.PENDING.value,
        YoloTrainStatus.PROCESSING.value,
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57021_YOLO_TRAIN_JOB_NOT_PAUSABLE,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    _phase, _label, runtime_pid, _detail = _read_train_phase(job_id)
    mark_user_pause_requested(job_id)
    request_stop_train_job(job_id)
    _terminate_train_process_tree(job_id, runtime_pid)
    _mark_job_cancelled(job_id, db=db, runtime_pid=runtime_pid)
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    return _job_to_response(db, job)


def apply_runtime_hyperparams(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
    hyperparams: dict[str, Any],
) -> YoloTrainJobResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    runtime_hp = normalize_runtime_editable_hyperparams(hyperparams)
    if not runtime_hp:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="没有可应用的临时训练超参数",
        )

    if job.status == YoloTrainStatus.PROCESSING.value:
        _write_pending_runtime_hyperparams(job_id, runtime_hp)
        _append_train_process_log(
            job_id,
            "runtime_hyperparams_pending detail=will restart after current epoch",
        )
        return _job_to_response(db, job)

    if str(job.status or "") not in (
        YoloTrainStatus.FAILED.value,
        YoloTrainStatus.CANCELLED.value,
        YoloTrainStatus.PENDING.value,
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57020_YOLO_TRAIN_JOB_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前训练作业状态不支持调参续训",
        )

    with begin_transaction(db):
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job:
            raise LabelUException(
                code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        _apply_runtime_hyperparams_to_job_row(db, job, runtime_hp)
    _clear_pending_runtime_hyperparams(job_id)
    if not _restart_train_job_from_existing_job(db, job_id):
        raise LabelUException(
            code=ErrorCode.CODE_57020_YOLO_TRAIN_JOB_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="无法按新超参数继续运行训练作业",
        )
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    return _job_to_response(db, job)


def delete_train_job(
    db: Session, task_id: int, job_id: int, current_user: User
) -> None:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if _job_is_actually_training(job):
        raise LabelUException(
            code=ErrorCode.CODE_57020_YOLO_TRAIN_JOB_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练作业正在运行中，请先暂停或等待结束后再删除",
        )
    if crud_yolo_bm1688_export.has_active_export_for_job(db, yolo_train_job_id=job_id):
        raise LabelUException(
            code=ErrorCode.CODE_57020_YOLO_TRAIN_JOB_BUSY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="存在进行中的 BM1688 导出，请等待完成或取消后再删除",
        )

    _detach_train_job_runtime(job_id)

    exports = crud_yolo_bm1688_export.list_all_for_job(db, yolo_train_job_id=job_id)
    export_snapshots = [
        {
            "id": int(e.id),
            "onnx_rel_path": e.onnx_rel_path,
            "bmodel_rel_path": e.bmodel_rel_path,
        }
        for e in exports
    ]
    weights_snapshot = job.weights_path
    with begin_transaction(db):
        # 须先于 export / job 删除：yolo_board_validation 外键指向二者
        crud_yolo_board_validation.delete_all_for_job(db, yolo_train_job_id=job_id)
        # yolo_edge_validation_job.export_id 外键指向 yolo_bm1688_export.id。
        # 必须先清理边端验证记录，否则 MySQL 会拒绝删除导出记录。
        for export in exports:
            crud_yolo_edge_validation_job.delete_all_for_export(
                db, export_id=int(export.id)
            )
        crud_yolo_eval_report.delete_all_for_job(db, job_id=job_id)
        crud_yolo_bm1688_export.delete_all_for_job(db, yolo_train_job_id=job_id)
        strategy_row = crud_strategy_task_integration.get_by_derived_train_job_id(
            db, derived_train_job_id=job_id
        )
        if strategy_row:
            crud_strategy_task_integration.update(
                db=db,
                row=strategy_row,
                obj_in={
                    StrategyTaskIntegration.platform_result_json.key: None,
                    StrategyTaskIntegration.automation_result_json.key: None,
                    StrategyTaskIntegration.platform_error_json.key: None,
                    StrategyTaskIntegration.derived_train_job_id.key: None,
                    StrategyTaskIntegration.derived_export_id.key: None,
                    StrategyTaskIntegration.derived_edge_validation_job_id.key: None,
                },
            )
        crud_yolo_train_job.delete_row(db, job)

    _purge_train_job_disk(
        job_id,
        weights_path=weights_snapshot,
        export_snapshots=export_snapshots,
    )
    logger.info(
        "deleted yolo train job id={} task_id={} user_id={} exports={}",
        job_id,
        task_id,
        current_user.id,
        len(exports),
    )


def list_train_jobs(
    db: Session, task_id: int, current_user: User
) -> List[YoloTrainJobResponse]:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    jobs = crud_yolo_train_job.list_by_task_id(db=db, task_id=task_id)
    return [_job_to_response(db, j, task) for j in jobs]


def resolve_weights_path(stored: str | None) -> Path:
    if not stored:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    p = Path(stored)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / p
    resolved = p.resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    if not str(resolved).startswith(str(root)):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not resolved.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return resolved
