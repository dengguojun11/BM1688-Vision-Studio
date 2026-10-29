"""Hard-delete task-owned database rows and local artifacts.

Task deletion used to be a soft delete, which left imported samples and files
behind.  This module keeps the destructive work in one place so the HTTP
endpoint and maintenance scripts use the same dependency order.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from loguru import logger
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.db import Base
from labelu.internal.domain.models.annotation_contract import TaskAnnotationContract
from labelu.internal.domain.models.experiment_dataset_group import (
    ExperimentDatasetGroup,
)
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


@dataclass
class TaskCleanupResult:
    """Paths and generated artifacts that can be removed after commit."""

    attachment_paths: set[str] = field(default_factory=set)
    train_job_ids: set[int] = field(default_factory=set)
    export_ids: set[int] = field(default_factory=set)
    deleted_rows: dict[str, int] = field(default_factory=dict)


def _table(name: str):
    return Base.metadata.tables.get(name)


def _database_table_names(db: Session) -> set[str]:
    """Return tables that actually exist in the bound database.

    The application metadata can contain retired/optional tables that are not
    present in an older local SQLite database.  Cache the inspection for the
    lifetime of this cleanup session so checking optional tables does not add
    one schema query per row operation.
    """

    cached = db.info.get("task_cleanup_database_table_names")
    if cached is None:
        bind = db.get_bind()
        cached = set(inspect(bind).get_table_names())
        db.info["task_cleanup_database_table_names"] = cached
    return cached


def _delete_by_ids(db: Session, table_name: str, column_name: str, ids: Iterable[int]) -> int:
    values = sorted({int(value) for value in ids if value is not None})
    table = _table(table_name)
    if not values or table is None or column_name not in table.c:
        return 0
    if table_name not in _database_table_names(db):
        logger.warning(
            "task cleanup skipped missing database table table={} column={}",
            table_name,
            column_name,
        )
        return 0
    result = db.execute(delete(table).where(table.c[column_name].in_(values)))
    count = int(result.rowcount or 0)
    if count:
        logger.debug("task cleanup table={} column={} rows={}", table_name, column_name, count)
    return count


def _parse_task_ids(raw: Any) -> set[int]:
    if not raw:
        return set()
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return set()
    if not isinstance(value, list):
        return set()
    result: set[int] = set()
    for item in value:
        if isinstance(item, dict):
            item = item.get("task_id")
        try:
            if item is not None:
                result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _remove_task_id_from_json(raw: Any, task_id: int) -> str | None:
    if not raw:
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return raw
    if not isinstance(value, list):
        return raw
    if value and isinstance(value[0], dict):
        value = [item for item in value if int(item.get("task_id", -1)) != task_id]
    else:
        value = [item for item in value if str(item) != str(task_id)]
    return json.dumps(value, ensure_ascii=False)


def _replacement_task_id(db: Session, source_ids: set[int], task_id: int) -> int | None:
    candidates = sorted(source_ids - {task_id})
    if not candidates:
        return None
    existing = {
        int(value)
        for value in db.execute(select(Task.id).where(Task.id.in_(candidates))).scalars()
    }
    active = {
        int(value)
        for value in db.execute(
            select(Task.id).where(Task.id.in_(sorted(existing)), Task.deleted_at.is_(None))
        ).scalars()
    }
    return next((candidate for candidate in candidates if candidate in active), None)


def cleanup_task_records(db: Session, task_id: int) -> TaskCleanupResult:
    """Delete all rows owned by ``task_id`` inside the caller's transaction.

    Shared training jobs/exports are retained and reassigned to a remaining
    source task.  The returned result is safe to pass to
    :func:`remove_task_artifacts` after the transaction commits.
    """

    task_id = int(task_id)
    result = TaskCleanupResult()
    task = db.query(Task).filter(Task.id == task_id).first()
    if task is None:
        return result

    samples = db.query(TaskSample.id, TaskSample.file_id).filter(TaskSample.task_id == task_id).all()
    sample_ids = {int(row.id) for row in samples}
    attachment_ids = {int(row.file_id) for row in samples if row.file_id is not None}
    attachments = db.query(TaskAttachment.id, TaskAttachment.path).filter(TaskAttachment.task_id == task_id).all()
    attachment_ids.update(int(row.id) for row in attachments)
    result.attachment_paths.update(str(row.path) for row in attachments if row.path)

    contracts = db.query(TaskAnnotationContract.id).filter(TaskAnnotationContract.task_id == task_id).all()
    contract_ids = {int(row.id) for row in contracts}

    # Rows below depend on samples, attachments, or contracts.
    result.deleted_rows["sample_annotation_label_index"] = _delete_by_ids(
        db, "sample_annotation_label_index", "sample_id", sample_ids
    )
    result.deleted_rows["sample_annotation_scope"] = _delete_by_ids(
        db, "sample_annotation_scope", "sample_id", sample_ids
    )
    result.deleted_rows["sample_annotation_scope_by_contract"] = _delete_by_ids(
        db, "sample_annotation_scope", "contract_id", contract_ids
    )
    result.deleted_rows["task_sample_updater"] = _delete_by_ids(
        db, "task_sample_updater", "sample_id", sample_ids
    )
    result.deleted_rows["sample_edit_log"] = _delete_by_ids(
        db, "sample_edit_log", "sample_id", sample_ids
    ) + _delete_by_ids(db, "sample_edit_log", "task_id", {task_id})
    result.deleted_rows["task_pre_annotation"] = _delete_by_ids(
        db, "task_pre_annotation", "task_id", {task_id}
    )
    result.deleted_rows["task_annotation_label"] = _delete_by_ids(
        db, "task_annotation_label", "task_id", {task_id}
    ) + _delete_by_ids(db, "task_annotation_label", "contract_id", contract_ids)

    contract_table = _table("task_annotation_contract")
    if contract_ids and contract_table is not None:
        # Break the self-reference before deleting the contract rows.
        db.execute(
            update(contract_table)
            .where(contract_table.c.parent_contract_id.in_(sorted(contract_ids)))
            .values(parent_contract_id=None)
        )
    result.deleted_rows["task_annotation_contract"] = _delete_by_ids(
        db, "task_annotation_contract", "task_id", {task_id}
    )

    # Task-scoped background jobs.  Export/edge rows are ordered explicitly
    # because the schema does not declare cascading deletes for these FKs.
    result.deleted_rows["yolo_preannotate_job"] = _delete_by_ids(
        db, "yolo_preannotate_job", "task_id", {task_id}
    )
    result.deleted_rows["yolo_edge_validation_job"] = _delete_by_ids(
        db, "yolo_edge_validation_job", "task_id", {task_id}
    )
    result.deleted_rows["yolo_board_validation"] = _delete_by_ids(
        db, "yolo_board_validation", "task_id", {task_id}
    )
    for table_name in (
        "auto_label_job",
        "edge_device_job",
        "export_job",
        "locateanything_preannotate_job",
        "sample_bounds_check_job",
        "tagtool_preannotate_job",
    ):
        result.deleted_rows[table_name] = _delete_by_ids(db, table_name, "task_id", {task_id})

    train_jobs = db.query(YoloTrainJob).filter(YoloTrainJob.task_id == task_id).all()
    train_job_by_id = {int(job.id): job for job in train_jobs}
    exclusive_train_job_ids: set[int] = set()
    for job in train_jobs:
        source_ids = _parse_task_ids(job.source_task_ids_json)
        replacement = _replacement_task_id(db, source_ids, task_id)
        if replacement is not None:
            job.task_id = replacement
            job.source_task_ids_json = _remove_task_id_from_json(
                job.source_task_ids_json, task_id
            )
        else:
            exclusive_train_job_ids.add(int(job.id))

    exports = db.query(YoloBm1688Export).filter(YoloBm1688Export.task_id == task_id).all()
    exclusive_export_ids: set[int] = set()
    for export in exports:
        job = train_job_by_id.get(int(export.yolo_train_job_id))
        source_ids = _parse_task_ids(job.source_task_ids_json) if job else set()
        replacement = _replacement_task_id(db, source_ids, task_id)
        if replacement is not None:
            export.task_id = replacement
        else:
            exclusive_export_ids.add(int(export.id))

    # Export dependents must go before export rows.
    result.deleted_rows["yolo_edge_validation_job_by_export"] = _delete_by_ids(
        db, "yolo_edge_validation_job", "export_id", exclusive_export_ids
    )
    result.deleted_rows["yolo_board_validation_by_export"] = _delete_by_ids(
        db, "yolo_board_validation", "export_id", exclusive_export_ids
    )
    for export_id in exclusive_export_ids:
        export_row = db.query(YoloBm1688Export).filter(YoloBm1688Export.id == export_id).first()
        if export_row is not None:
            db.delete(export_row)
    result.export_ids.update(exclusive_export_ids)

    # Evaluation reports and exports reference training jobs.
    result.deleted_rows["yolo_eval_report"] = _delete_by_ids(
        db, "yolo_eval_report", "yolo_train_job_id", exclusive_train_job_ids
    )
    result.deleted_rows["yolo_board_validation_by_job"] = _delete_by_ids(
        db, "yolo_board_validation", "yolo_train_job_id", exclusive_train_job_ids
    )
    for job_id in exclusive_train_job_ids:
        job = train_job_by_id.get(job_id)
        if job is not None:
            db.delete(job)
    result.train_job_ids.update(exclusive_train_job_ids)

    training_rows = db.query(TrainingTask).filter(TrainingTask.primary_task_id == task_id).all()
    for training_task in training_rows:
        source_ids = _parse_task_ids(training_task.source_tasks_snapshot_json)
        replacement = _replacement_task_id(db, source_ids, task_id)
        if replacement is not None:
            training_task.primary_task_id = replacement
        else:
            db.query(YoloTrainJob).filter(
                YoloTrainJob.training_task_id == training_task.id
            ).update({YoloTrainJob.training_task_id: None}, synchronize_session=False)
            db.delete(training_task)

    # Integration receipts/import bookkeeping is task-owned.  Groups are
    # retained only when another package still references them.
    integrations = db.query(StrategyTaskIntegration.id).filter(
        StrategyTaskIntegration.labelu_task_id == task_id
    ).all()
    integration_ids = {int(row.id) for row in integrations}
    result.deleted_rows["strategy_platform_callback_receipt"] = _delete_by_ids(
        db, "strategy_platform_callback_receipt", "strategy_task_integration_id", integration_ids
    )
    result.deleted_rows["strategy_task_integration"] = _delete_by_ids(
        db, "strategy_task_integration", "labelu_task_id", {task_id}
    )

    import_candidates = db.query(ExperimentDatasetImport).filter(
        (ExperimentDatasetImport.draft_task_id == task_id)
        | ExperimentDatasetImport.formal_task_ids_json.contains(f"{task_id}")
    ).all()
    import_rows = [
        row
        for row in import_candidates
        if row.draft_task_id == task_id
        or task_id in _parse_task_ids(row.formal_task_ids_json)
    ]
    group_ids: set[int] = set()
    for row in import_rows:
        if row.aggregation_group_id is not None:
            group_ids.add(int(row.aggregation_group_id))
        formal_ids = _parse_task_ids(row.formal_task_ids_json)
        formal_ids.discard(task_id)
        if formal_ids:
            row.formal_task_ids_json = json.dumps(sorted(formal_ids))
            if row.draft_task_id == task_id:
                row.draft_task_id = None
        else:
            db.delete(row)

    groups = db.query(ExperimentDatasetGroup).filter(
        (ExperimentDatasetGroup.formal_task_id == task_id)
        | ExperimentDatasetGroup.id.in_(sorted(group_ids) if group_ids else [-1])
    ).all()
    for group in groups:
        remaining = db.query(ExperimentDatasetImport.id).filter(
            ExperimentDatasetImport.aggregation_group_id == group.id
        ).first()
        if remaining is None:
            db.delete(group)
        else:
            group.formal_task_id = None

    # Samples must be removed before attachments; attachment rows are kept only
    # when a remaining task still references the file.
    result.deleted_rows["task_sample"] = _delete_by_ids(db, "task_sample", "task_id", {task_id})
    result.deleted_rows["task_collaborator"] = _delete_by_ids(
        db, "task_collaborator", "task_id", {task_id}
    )
    owned_attachment_ids = {int(row.id) for row in attachments}
    remaining_sample_file_ids = {
        int(value)
        for value in db.execute(
            select(TaskSample.file_id).where(TaskSample.file_id.in_(sorted(attachment_ids)))
        ).scalars()
        if value is not None
    }
    remaining_pre_annotation_file_ids = {
        int(value)
        for value in db.execute(
            select(TaskPreAnnotation.file_id).where(
                TaskPreAnnotation.file_id.in_(sorted(attachment_ids))
            )
        ).scalars()
        if value is not None
    }
    safe_attachment_ids = owned_attachment_ids - remaining_sample_file_ids - remaining_pre_annotation_file_ids
    result.deleted_rows["task_attachment"] = _delete_by_ids(
        db, "task_attachment", "id", safe_attachment_ids
    )

    # Keep the label-set claim so a future sync can recreate the pool task,
    # while releasing the FK that would otherwise block the task hard-delete.
    pool_registry = _table("yolo_consistent_pool_registry")
    if pool_registry is not None:
        unbound = db.execute(
            update(pool_registry)
            .where(pool_registry.c.task_id == task_id)
            .values(task_id=None)
        )
        result.deleted_rows["yolo_consistent_pool_registry_unbound"] = int(
            unbound.rowcount or 0
        )

    # Finally remove the task row itself.  All direct FKs have been handled.
    db.flush()
    result.deleted_rows["task"] = _delete_by_ids(db, "task", "id", {task_id})
    return result


def _safe_media_path(raw_path: str) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw or raw.startswith(("s3://", "http://", "https://")):
        return None
    root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning("skip task cleanup path outside media root: {}", raw)
        return None
    return candidate


def remove_task_artifacts(
    db: Session, task_id: int, result: TaskCleanupResult
) -> None:
    """Remove local files after the database transaction has committed."""

    paths = {str(value) for value in db.execute(
        select(TaskAttachment.path).where(TaskAttachment.path.in_(sorted(result.attachment_paths)))
    ).scalars() if value}
    for raw_path in result.attachment_paths - paths:
        candidate = _safe_media_path(raw_path)
        if candidate is None:
            continue
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            elif candidate.exists():
                candidate.unlink()
        except OSError as exc:
            logger.warning("remove task attachment failed task_id={} path={}: {}", task_id, raw_path, exc)

    root = Path(settings.MEDIA_ROOT).resolve()
    task_upload_dir = (root / settings.UPLOAD_DIR / str(int(task_id))).resolve()
    try:
        task_upload_dir.relative_to(root / settings.UPLOAD_DIR)
        if task_upload_dir.exists():
            shutil.rmtree(task_upload_dir)
    except (OSError, ValueError) as exc:
        logger.warning("remove task upload directory failed task_id={}: {}", task_id, exc)

    for job_id in result.train_job_ids:
        _remove_tree(root / "yolo_train" / f"job-{job_id}")
    for export_id in result.export_ids:
        _remove_tree(root / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id))


def _remove_tree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path)
    except OSError as exc:
        logger.warning("remove task artifact directory failed path={}: {}", path, exc)
