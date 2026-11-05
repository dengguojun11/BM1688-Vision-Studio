"""Resolve third-party model ids into local label contracts and auto-create
the same training job an operator would create from the training center.

The legacy strategy-evolution intake used to require a published local export
(weights + source task) before it could start training.  AICM-synchronised
models only carry model-set metadata and labels, so those tasks stopped at
``waiting_for_model``.  This module implements the agreed fallback:

1. resolve the incoming model id in the published catalog (including AICM
   synchronised ``remote_legacy`` rows, fetched on demand when missing);
2. take the model label contract and find a local annotated image task whose
   label contract partially overlaps it (configurable ratio/count);
3. pick the newest completed training job of that task as initial weights when
   one exists, otherwise use the configured preset weights (yolo11n.pt);
4. create training through the same label-dimension entrypoint as the manual
   "新建训练作业" flow so the dataset snapshot, selection contract, lineage and
   downstream pipeline stay identical.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.application.command.training_task import (
    CreateLabelDimensionTrainingTaskCommand,
)
from labelu.internal.application.service import published_model_catalog
from labelu.internal.application.service import training_task as training_task_service
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.annotation_contract import TaskAnnotationLabel
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

DEFAULT_TRAINING_RECIPE = "yolov11_detection"
IMAGE_MEDIA_TYPE = "IMAGE"
DRAFT_TASK_STATUS = "DRAFT"
COMPLETED_JOB_STATUS = "COMPLETED"


def _json_loads_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_time_text(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _normalized_labels(labels: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in labels or []:
        value = str(item or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _task_label_values(db: Session, task_id: int) -> list[str]:
    rows = (
        db.query(AnnotationLabel.label_value)
        .join(
            TaskAnnotationLabel,
            TaskAnnotationLabel.label_id == AnnotationLabel.id,
        )
        .filter(
            TaskAnnotationLabel.task_id == int(task_id),
            TaskAnnotationLabel.status == "active",
            AnnotationLabel.status == "active",
            AnnotationLabel.annotation_kind == "bbox",
        )
        .all()
    )
    return _normalized_labels([item[0] for item in rows])


def _catalog_contract_labels(db: Session, row: Any) -> list[str]:
    try:
        declared = json.loads(str(row.labels_json or ""))
    except (TypeError, ValueError):
        declared = []
    labels = _normalized_labels(declared if isinstance(declared, list) else [])
    if labels:
        return labels
    # Local publish rows may not carry labels_json; derive them from the
    # export/training source task contract instead.
    task_id = _safe_int(getattr(row, "task_id", None))
    if task_id:
        return _task_label_values(db, task_id)
    export_id = _safe_int(getattr(row, "export_id", None))
    if export_id:
        from labelu.internal.adapter.persistence import crud_yolo_bm1688_export

        export_row = crud_yolo_bm1688_export.get(db, export_id)
        if export_row and getattr(export_row, "task_id", None):
            return _task_label_values(db, int(export_row.task_id))
    return []


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def resolve_model_contract(db: Session, model_id: str) -> dict[str, Any] | None:
    """Resolve an external model id to catalog identity + labels.

    Resolution order: local published catalog (including AICM-synced rows),
    on-demand AICM model-set lookup, then local export/training fallback.
    """
    text = str(model_id or "").strip()
    if not text:
        return None
    catalog_row = None
    try:
        catalog_row = published_model_catalog.resolve_model(db, text)
    except LabelUException as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
    if catalog_row is None:
        catalog_row = published_model_catalog.ensure_remote_model(db, text)
    if catalog_row is None:
        return None
    labels = _catalog_contract_labels(db, catalog_row)
    return {
        "model_id": text,
        "resolved_model_id": str(catalog_row.public_model_id or text),
        "catalog_id": int(catalog_row.id),
        "display_name": str(catalog_row.display_name or catalog_row.name or text),
        "source_type": str(catalog_row.source_type or ""),
        "aicm_model_set_id": _safe_int(catalog_row.aicm_model_set_id),
        "labels": labels,
        "label_count": len(labels),
    }


def _candidate_task_labels(
    db: Session,
) -> dict[int, tuple[Task, list[str]]]:
    rows = (
        db.query(Task, AnnotationLabel.label_value)
        .join(
            TaskAnnotationLabel,
            TaskAnnotationLabel.task_id == Task.id,
        )
        .join(
            AnnotationLabel,
            AnnotationLabel.id == TaskAnnotationLabel.label_id,
        )
        .filter(
            Task.deleted_at.is_(None),
            Task.media_type == IMAGE_MEDIA_TYPE,
            Task.status != DRAFT_TASK_STATUS,
            TaskAnnotationLabel.status == "active",
            AnnotationLabel.status == "active",
            AnnotationLabel.annotation_kind == "bbox",
        )
        .all()
    )
    tasks: dict[int, tuple[Task, list[str]]] = {}
    for task, label_value in rows:
        task_id = int(task.id)
        current = tasks.get(task_id)
        if current is None:
            tasks[task_id] = (task, [str(label_value)])
        else:
            current[1].append(str(label_value))
    return tasks


def _done_sample_counts(db: Session, task_ids: list[int]) -> dict[int, int]:
    if not task_ids:
        return {}
    rows = (
        db.query(TaskSample.task_id, func.count(TaskSample.id))
        .filter(
            TaskSample.task_id.in_(task_ids),
            TaskSample.state == SampleState.DONE.value,
            TaskSample.deleted_at.is_(None),
        )
        .group_by(TaskSample.task_id)
        .all()
    )
    return {int(task_id): int(count) for task_id, count in rows}


def select_label_matched_task(
    db: Session,
    model_labels: list[str],
    *,
    min_ratio: float | None = None,
    min_count: int | None = None,
) -> dict[str, Any] | None:
    """Pick the local annotated task that best covers the model labels.

    Partial overlap is allowed (the third-party contract only needs a subset of
    its labels to exist in a local label contract), preferring the task with
    the most matched labels and then the largest completed sample pool.
    """
    labels = _normalized_labels(model_labels)
    if not labels:
        return None
    ratio_threshold = (
        float(settings.STRATEGY_EVOLUTION_LABEL_MATCH_MIN_RATIO)
        if min_ratio is None
        else float(min_ratio)
    )
    count_threshold = (
        int(settings.STRATEGY_EVOLUTION_LABEL_MATCH_MIN_COUNT)
        if min_count is None
        else int(min_count)
    )
    required_matches = max(
        1,
        min(
            len(labels), max(count_threshold, math.ceil(len(labels) * ratio_threshold))
        ),
    )
    wanted = {item.lower(): item for item in labels}
    candidates: list[dict[str, Any]] = []
    for task_id, (task, task_labels) in _candidate_task_labels(db).items():
        matched = _normalized_labels(
            [wanted[item.lower()] for item in task_labels if item.lower() in wanted]
        )
        if len(matched) < required_matches:
            continue
        candidates.append(
            {
                "task": task,
                "matched_labels": matched,
                "matched_count": len(matched),
                "model_label_count": len(labels),
                "match_ratio": round(len(matched) / len(labels), 4),
            }
        )
    if not candidates:
        return None
    counts = _done_sample_counts(db, [item["task"].id for item in candidates])
    for item in candidates:
        item["done_sample_count"] = counts.get(int(item["task"].id), 0)
    candidates.sort(
        key=lambda item: (
            item["matched_count"],
            item["match_ratio"],
            item["done_sample_count"],
            -int(item["task"].id),
        ),
        reverse=True,
    )
    selected = candidates[0]
    if selected["done_sample_count"] <= 0:
        return None
    return selected


def select_validation_sample_ids(
    db: Session, task_id: int, *, limit: int | None = None
) -> list[int]:
    max_samples = int(
        limit
        if limit is not None
        else getattr(settings, "YOLO_EDGE_VALIDATION_MAX_IMAGES", 50)
    )
    max_samples = max(1, min(max_samples, 500))
    rows = (
        db.query(TaskSample.id)
        .filter(
            TaskSample.task_id == int(task_id),
            TaskSample.state == SampleState.DONE.value,
            TaskSample.deleted_at.is_(None),
        )
        .order_by(TaskSample.id.asc())
        .limit(max_samples)
        .all()
    )
    return [int(item[0]) for item in rows]


def resolve_initial_weights(db: Session, task_id: int) -> dict[str, Any]:
    job = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.task_id == int(task_id),
            YoloTrainJob.status == COMPLETED_JOB_STATUS,
            YoloTrainJob.weights_path.isnot(None),
        )
        .order_by(YoloTrainJob.id.desc())
        .first()
    )
    if job is not None:
        return {
            "weights_source": "trained",
            "weights_ref_id": int(job.id),
            "model_weights": str(job.model_weights or "yolo11n.pt"),
            "resolved_by": "local_completed_training_job",
        }
    return {
        "weights_source": "preset",
        "weights_ref_id": None,
        "model_weights": str(
            settings.STRATEGY_EVOLUTION_MODEL_TRAIN_WEIGHTS or "yolo11n.pt"
        ),
        "resolved_by": "preset_weights",
    }


def _training_task_name(db: Session, admin: User, row: StrategyTaskIntegration) -> str:
    base = str(row.task_name or "模型优化").strip() or "模型优化"
    tail = str(row.external_task_id or "").strip()[-10:]
    candidate = f"{base[:96]}-{tail}"[:128]
    if training_task_service.check_training_task_name_available(
        db, admin.id, candidate
    ):
        return candidate
    for index in range(2, 50):
        suffix = f"-{index}"
        trimmed = f"{candidate[: 128 - len(suffix)]}{suffix}"
        if training_task_service.check_training_task_name_available(
            db, admin.id, trimmed
        ):
            return trimmed
    raise LabelUException(
        code=30003,
        status_code=400,
        message="自动训练任务名称生成失败，请检查任务名",
    )


def _existing_training_job(
    db: Session, row: StrategyTaskIntegration
) -> YoloTrainJob | None:
    return (
        db.query(YoloTrainJob)
        .filter(
            (YoloTrainJob.source_integration_id == int(row.id))
            | (
                (YoloTrainJob.source_system == "strategy_evolution")
                & (YoloTrainJob.source_external_task_id == row.external_task_id)
            ),
            YoloTrainJob.status.notin_(
                (YoloTrainStatus.CANCELLED.value, YoloTrainStatus.FAILED.value)
            ),
        )
        .order_by(YoloTrainJob.id.desc())
        .first()
    )


def _result_from_job(
    db: Session,
    row: StrategyTaskIntegration,
    job: YoloTrainJob,
    *,
    contract: dict[str, Any] | None,
    selection: dict[str, Any] | None,
    weights: dict[str, Any] | None,
) -> dict[str, Any]:
    task_id = _safe_int(job.task_id)
    result: dict[str, Any] = {
        "model_training_status": "training_job_created",
        "message": "已创建模型训练任务",
        "derived_train_job_id": int(job.id),
        "derived_task_id": task_id,
        "baseline_available": False,
        "sample_selection_mode": "label_contract_match",
        "model_resolution": {
            **(contract or {}),
            "matched_task_id": int(selection["task"].id) if selection else task_id,
            "matched_task_name": (str(selection["task"].name) if selection else None),
            "matched_labels": (list(selection["matched_labels"]) if selection else []),
            "match_ratio": (float(selection["match_ratio"]) if selection else None),
            "weights": weights or {},
            "baseline_available": False,
        },
        "matched_task_id": int(selection["task"].id) if selection else task_id,
        "matched_task_name": str(selection["task"].name) if selection else None,
        "matched_labels": list(selection["matched_labels"]) if selection else [],
        "selected_sample_count": (
            int(selection["done_sample_count"]) if selection else None
        ),
    }
    if task_id:
        sample_ids = select_validation_sample_ids(db, int(task_id))
        if sample_ids:
            result["selected_sample_ids_by_task"] = {str(task_id): sample_ids}
    return result


def ensure_model_training_job_sync(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any] | None,
    admin: User,
) -> tuple[bool, dict[str, Any]]:
    """Ensure a training job exists for this strategy task (sync, idempotent)."""
    payload = dict(result or {})
    existing = _existing_training_job(db, row)
    if existing is not None:
        payload.update(
            _result_from_job(
                db,
                row,
                existing,
                contract=None,
                selection=None,
                weights=None,
            )
        )
        return True, payload

    target = _json_loads_object(row.target_json)
    model_id = str(target.get("model_id") or "").strip()
    contract = resolve_model_contract(db, model_id) if model_id else None
    if contract is None:
        payload["model_training_status"] = "waiting_for_model"
        payload["message"] = f"未在本地或 AICM 找到模型 {model_id or '-'} 的标签契约"
        payload["model_resolution"] = {
            "model_id": model_id,
            "reason": "model_not_found_in_catalog_or_aicm",
        }
        payload["model_lookup_attempted_at"] = _now_text()
        return False, payload

    labels = contract.get("labels") or []
    if not labels:
        payload["model_training_status"] = "waiting_for_model"
        payload["message"] = "模型没有可用的标签契约，无法自动选择数据集"
        payload["model_resolution"] = contract
        payload["model_lookup_attempted_at"] = _now_text()
        return False, payload

    selection = select_label_matched_task(db, labels)
    if selection is None:
        payload["model_training_status"] = "waiting_for_annotations"
        payload["message"] = (
            "本系统标签契约未命中模型标签（需至少命中 "
            f"{settings.STRATEGY_EVOLUTION_LABEL_MATCH_MIN_COUNT} 个或 "
            f"{round(float(settings.STRATEGY_EVOLUTION_LABEL_MATCH_MIN_RATIO) * 100)}%），"
            "暂无可自动训练的数据集"
        )
        payload["model_resolution"] = contract
        payload["model_lookup_attempted_at"] = _now_text()
        return False, payload

    task = selection["task"]
    weights = resolve_initial_weights(db, int(task.id))
    from labelu.internal.application.service import yolo_train as yolo_train_service

    cuda_ok, gpu_count = yolo_train_service.gpu_training_available()
    if bool(getattr(settings, "YOLO_TRAIN_REQUIRE_GPU", True)) and (
        not cuda_ok or int(gpu_count) <= 0
    ):
        payload["model_training_status"] = "waiting_for_gpu"
        payload["message"] = (
            "GPU 不可用，已禁止 CPU 训练；等待 GPU 恢复后自动重试"
        )
        payload["model_resolution"] = {
            **contract,
            "matched_task_id": int(task.id),
            "matched_task_name": str(task.name),
            "matched_labels": list(selection["matched_labels"]),
            "weights": weights,
        }
        payload["model_lookup_attempted_at"] = _now_text()
        return False, payload
    generation = (
        int(
            db.query(func.count(YoloTrainJob.id))
            .filter(YoloTrainJob.source_integration_id == int(row.id))
            .scalar()
            or 0
        )
        + 1
    )
    command = CreateLabelDimensionTrainingTaskCommand(
        name=_training_task_name(db, admin, row),
        selected_task_ids=[int(task.id)],
        dataset_id=int(task.dataset_id) if task.dataset_id else None,
        dataset_category_id=(
            int(task.dataset_category_id) if task.dataset_category_id else None
        ),
        model_weights=weights["model_weights"],
        weights_source=weights["weights_source"],
        weights_ref_id=weights["weights_ref_id"],
        epochs=int(settings.STRATEGY_EVOLUTION_MODEL_TRAIN_EPOCHS),
        imgsz=int(settings.STRATEGY_EVOLUTION_MODEL_TRAIN_IMGSZ),
        training_recipe_key=DEFAULT_TRAINING_RECIPE,
    )
    response = training_task_service.create_label_dimension_training_task(
        db=db,
        current_user=admin,
        cmd=command,
        idempotency_key=f"strategy:{row.external_task_id}:{generation}",
    )
    job = crud_yolo_train_job.get(db, int(response.latest_job_id))
    if job is None:
        raise LabelUException(
            code=30003,
            status_code=500,
            message="自动训练作业创建后未找到作业记录",
        )
    with begin_transaction(db):
        job.source_type = "third_party"
        job.source_system = "strategy_evolution"
        job.source_external_task_id = row.external_task_id
        job.source_external_package_id = row.source_package_id
        job.source_integration_id = int(row.id)
        if row.dataset_category_id:
            job.dataset_category_id = int(row.dataset_category_id)
        db.add(job)
    payload = _result_from_job(
        db,
        row,
        job,
        contract=contract,
        selection=selection,
        weights=weights,
    )
    payload["training_task_id"] = int(response.training_task_id)
    logger.info(
        "strategy evolution auto training job created external_task_id={} job_id={} "
        "task_id={} matched_labels={} weights_source={}",
        row.external_task_id,
        job.id,
        task.id,
        selection["matched_count"],
        weights["weights_source"],
    )
    return True, payload


def model_lookup_due(result: dict[str, Any] | None) -> bool:
    """Respect the configured retry interval for repeated contract lookups."""
    payload = result or {}
    last = _parse_time_text(payload.get("model_lookup_attempted_at"))
    if last is None:
        return True
    elapsed = (datetime.now() - last).total_seconds()
    return elapsed >= int(settings.STRATEGY_EVOLUTION_MODEL_LOOKUP_RETRY_SECONDS)
