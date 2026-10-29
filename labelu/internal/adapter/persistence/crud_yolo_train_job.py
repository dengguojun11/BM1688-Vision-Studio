from datetime import datetime
from typing import List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session, aliased

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus
from labelu.internal.domain.models.model_series import ModelSeries, ModelVariant
from labelu.internal.common.task_access import task_visibility_clause


def create(
    db: Session,
    task_id: int,
    user_id: int,
    model_weights: str,
    epochs: int,
    imgsz: int,
    category: str = "",
    hyperparams_json: Optional[str] = None,
    source_task_ids_json: Optional[str] = None,
    execution_mode: str = "local",
    train_node_id: Optional[str] = None,
    training_task_id: Optional[int] = None,
    job_type: str = "yolo_detection",
    component_status_json: Optional[str] = None,
    component_artifacts_json: Optional[str] = None,
    dataset_manifest_json: Optional[str] = None,
    training_recipe_key: Optional[str] = None,
    training_recipe_version: Optional[int] = None,
    project_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    idempotency_request_hash: Optional[str] = None,
    series_id: Optional[int] = None,
    variant_id: Optional[int] = None,
    parent_model_version_id: Optional[int] = None,
    parent_training_job_id: Optional[int] = None,
    branch_key: Optional[str] = None,
    selection_contract_sha256: Optional[str] = None,
    selection_contract_json: Optional[str] = None,
    sample_conditions_json: Optional[str] = None,
    inheritance_mode: Optional[str] = None,
) -> YoloTrainJob:
    if project_id is None:
        source_task = db.query(Task.project_id).filter(Task.id == int(task_id)).first()
        project_id = int(source_task[0]) if source_task and source_task[0] is not None else None
    job = YoloTrainJob(
        training_task_id=training_task_id,
        job_type=(job_type or "yolo_detection").strip() or "yolo_detection",
        task_id=task_id,
        project_id=project_id,
        created_by=user_id,
        model_weights=model_weights,
        epochs=epochs,
        imgsz=imgsz,
        category=category or "",
        hyperparams_json=hyperparams_json,
        source_task_ids_json=source_task_ids_json,
        dataset_manifest_json=dataset_manifest_json,
        training_recipe_key=(training_recipe_key or "").strip() or None,
        training_recipe_version=training_recipe_version,
        execution_mode=(execution_mode or "local").strip() or "local",
        train_node_id=(train_node_id or "").strip() or None,
        component_status_json=component_status_json,
        component_artifacts_json=component_artifacts_json,
        idempotency_key=(idempotency_key or None),
        idempotency_request_hash=(idempotency_request_hash or None),
        series_id=series_id,
        variant_id=variant_id,
        parent_model_version_id=parent_model_version_id,
        parent_training_job_id=parent_training_job_id,
        branch_key=(branch_key or "").strip() or None,
        selection_contract_sha256=(selection_contract_sha256 or "").strip() or None,
        selection_contract_json=selection_contract_json,
        sample_conditions_json=sample_conditions_json,
        inheritance_mode=(inheritance_mode or "").strip() or None,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    return job


def get(db: Session, job_id: int) -> Optional[YoloTrainJob]:
    return db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).first()


def list_by_task_id(db: Session, task_id: int) -> List[YoloTrainJob]:
    return (
        db.query(YoloTrainJob)
        .filter(YoloTrainJob.task_id == task_id)
        .order_by(YoloTrainJob.id.desc())
        .all()
    )


def list_jobs_for_user(
    db: Session,
    user_id: int,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    category: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    task_ids: Optional[List[int]] = None,
    status: Optional[str] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    page: int = 0,
    size: int = 20,
    task_access_unrestricted: bool = False,
    task_access_builtin_admin: bool = False,
    project_id: Optional[int] = None,
) -> Tuple[List[Tuple[YoloTrainJob, Task]], int]:
    """当前用户可访问的图像任务下的训练作业，按作业 id 倒序分页。"""
    base_filters = [
        Task.deleted_at == None,
        Task.media_type == MediaType.IMAGE.value,
    ]
    base_filters.append(
        task_visibility_clause(
            user_id=user_id,
            unrestricted=task_access_unrestricted,
            builtin_admin=task_access_builtin_admin,
        )
    )
    if project_id is not None:
        base_filters.append(YoloTrainJob.project_id == int(project_id))
    q = (
        db.query(YoloTrainJob, Task)
        .join(Task, Task.id == YoloTrainJob.task_id)
        .outerjoin(TrainingTask, TrainingTask.id == YoloTrainJob.training_task_id)
        .filter(*base_filters)
    )
    if task_name and task_name.strip():
        q = q.filter(Task.name.ilike(literal_like_pattern(task_name), escape=LIKE_ESCAPE))
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        dataset_match = (
            db.query(Dataset.id)
            .outerjoin(DatasetCategory, DatasetCategory.id == Dataset.dataset_category_id)
            .filter(
                Dataset.id == Task.dataset_id,
                or_(
                    Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                    Dataset.description.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.name.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.category_key.ilike(pattern, escape=LIKE_ESCAPE),
                ),
            )
            .exists()
        )
        q = q.filter(
            or_(
                cast(YoloTrainJob.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(YoloTrainJob.task_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.model_weights.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.status.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.execution_mode.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.train_node_id.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.category.ilike(pattern, escape=LIKE_ESCAPE),
                # Include immutable model lineage in the same free-text
                # contract as the task/dataset fields.  These columns are
                # what operators use to find a branch such as "雨天" or to
                # trace a rerun back to its parent job.
                YoloTrainJob.branch_key.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.selection_contract_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloTrainJob.selection_contract_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloTrainJob.sample_conditions_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloTrainJob.inheritance_mode.ilike(pattern, escape=LIKE_ESCAPE),
                cast(YoloTrainJob.series_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.variant_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.parent_model_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.parent_training_job_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingTask.name.ilike(pattern, escape=LIKE_ESCAPE),
                db.query(ModelSeries.id)
                .filter(
                    ModelSeries.id == YoloTrainJob.series_id,
                    or_(
                        ModelSeries.name.ilike(pattern, escape=LIKE_ESCAPE),
                        ModelSeries.model_family.ilike(pattern, escape=LIKE_ESCAPE),
                    ),
                )
                .exists(),
                db.query(ModelVariant.id)
                .filter(
                    ModelVariant.id == YoloTrainJob.variant_id,
                    or_(
                        ModelVariant.name.ilike(pattern, escape=LIKE_ESCAPE),
                        ModelVariant.branch_key.ilike(pattern, escape=LIKE_ESCAPE),
                        ModelVariant.selection_contract_sha256.ilike(
                            pattern, escape=LIKE_ESCAPE
                        ),
                    ),
                )
                .exists(),
                cast(Task.dataset_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                dataset_match,
            )
        )
    if category is not None and str(category).strip():
        q = q.filter(
            YoloTrainJob.category.ilike(
                literal_like_pattern(str(category).strip()), escape=LIKE_ESCAPE
            )
        )
    if dataset_category_id is not None:
        q = q.filter(YoloTrainJob.dataset_category_id == int(dataset_category_id))
    if task_ids is not None:
        if not task_ids:
            return [], 0
        q = q.filter(YoloTrainJob.task_id.in_(task_ids))
    if status and status.strip():
        q = q.filter(YoloTrainJob.status == status.strip())
    if created_from is not None:
        q = q.filter(YoloTrainJob.created_at >= created_from)
    if created_to is not None:
        q = q.filter(YoloTrainJob.created_at <= created_to)

    total = q.count()
    rows = (
        q.order_by(YoloTrainJob.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def list_completed_models_for_user(
    db: Session,
    user_id: int,
    *,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    dataset_category_id: Optional[int] = None,
    task_ids: Optional[List[int]] = None,
    page: int = 0,
    size: int = 20,
    task_access_unrestricted: bool = False,
    task_access_builtin_admin: bool = False,
    project_id: Optional[int] = None,
) -> Tuple[List[Tuple[YoloTrainJob, Task]], int]:
    """训练已完成且有权重的作业（用于模型下载列表）。"""
    base_filters = [
        Task.deleted_at == None,
        Task.media_type == MediaType.IMAGE.value,
        YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
        YoloTrainJob.weights_path.isnot(None),
        YoloTrainJob.weights_path != "",
    ]
    base_filters.append(
        task_visibility_clause(
            user_id=user_id,
            unrestricted=task_access_unrestricted,
            builtin_admin=task_access_builtin_admin,
        )
    )
    if project_id is not None:
        base_filters.append(YoloTrainJob.project_id == int(project_id))
    q = (
        db.query(YoloTrainJob, Task)
        .join(Task, Task.id == YoloTrainJob.task_id)
        .outerjoin(TrainingTask, TrainingTask.id == YoloTrainJob.training_task_id)
        .filter(*base_filters)
    )
    if task_name and task_name.strip():
        q = q.filter(
            Task.name.ilike(literal_like_pattern(task_name), escape=LIKE_ESCAPE)
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        dataset_match = (
            db.query(Dataset.id)
            .outerjoin(DatasetCategory, DatasetCategory.id == Dataset.dataset_category_id)
            .filter(
                Dataset.id == Task.dataset_id,
                or_(
                    Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                    Dataset.description.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.name.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.category_key.ilike(pattern, escape=LIKE_ESCAPE),
                ),
            )
            .exists()
        )
        category = aliased(DatasetCategory)
        task_or_job_category_match = (
            db.query(category.id)
            .filter(
                or_(
                    category.id == YoloTrainJob.dataset_category_id,
                    category.id == Task.dataset_category_id,
                    category.id == TrainingTask.dataset_category_id,
                ),
                or_(
                    category.name.ilike(pattern, escape=LIKE_ESCAPE),
                    category.category_key.ilike(pattern, escape=LIKE_ESCAPE),
                    category.description.ilike(pattern, escape=LIKE_ESCAPE),
                ),
            )
            .exists()
        )
        q = q.filter(
            or_(
                cast(YoloTrainJob.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(YoloTrainJob.training_task_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.task_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.source_task_ids_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.name.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.status.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.model_weights.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.weights_path.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.category.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.branch_key.ilike(pattern, escape=LIKE_ESCAPE),
                YoloTrainJob.selection_contract_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloTrainJob.selection_contract_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloTrainJob.sample_conditions_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                YoloTrainJob.inheritance_mode.ilike(pattern, escape=LIKE_ESCAPE),
                cast(YoloTrainJob.series_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.variant_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.parent_model_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(YoloTrainJob.parent_training_job_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                db.query(ModelSeries.id)
                .filter(
                    ModelSeries.id == YoloTrainJob.series_id,
                    or_(
                        ModelSeries.name.ilike(pattern, escape=LIKE_ESCAPE),
                        ModelSeries.model_family.ilike(pattern, escape=LIKE_ESCAPE),
                    ),
                )
                .exists(),
                db.query(ModelVariant.id)
                .filter(
                    ModelVariant.id == YoloTrainJob.variant_id,
                    or_(
                        ModelVariant.name.ilike(pattern, escape=LIKE_ESCAPE),
                        ModelVariant.branch_key.ilike(pattern, escape=LIKE_ESCAPE),
                        ModelVariant.selection_contract_sha256.ilike(
                            pattern, escape=LIKE_ESCAPE
                        ),
                    ),
                )
                .exists(),
                YoloTrainJob.status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(YoloTrainJob.dataset_category_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(Task.dataset_category_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(TrainingTask.dataset_category_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(Task.dataset_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                dataset_match,
                task_or_job_category_match,
            )
        )
    if dataset_category_id is not None:
        q = q.filter(YoloTrainJob.dataset_category_id == int(dataset_category_id))
    if task_ids is not None:
        if not task_ids:
            return [], 0
        q = q.filter(YoloTrainJob.task_id.in_(task_ids))

    total = q.count()
    rows = (
        q.order_by(YoloTrainJob.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def list_completed_with_weights(db: Session) -> List[YoloTrainJob]:
    return (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
            YoloTrainJob.weights_path.isnot(None),
            YoloTrainJob.weights_path != "",
        )
        .order_by(YoloTrainJob.id.asc())
        .all()
    )


def update_status(db: Session, job: YoloTrainJob, status: str, **kwargs) -> YoloTrainJob:
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    if job.training_task_id:
        training_task = (
            db.query(TrainingTask)
            .filter(
                TrainingTask.id == int(job.training_task_id),
                TrainingTask.latest_job_id == int(job.id),
            )
            .first()
        )
        if training_task is not None:
            training_task.latest_job_status = status
            db.add(training_task)
    db.flush()
    db.refresh(job)
    return job


def get_by_idempotency(
    db: Session, *, creator_id: int, idempotency_key: str
) -> Optional[YoloTrainJob]:
    return (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.created_by == int(creator_id),
            YoloTrainJob.idempotency_key == str(idempotency_key),
        )
        .first()
    )


def update_status_if_execution_lease_free(
    db: Session,
    job_id: int,
    status: str,
    *,
    now: datetime | None = None,
    **kwargs,
) -> YoloTrainJob | None:
    """Atomically update a legacy recovery row only when no active worker owns it.

    Stale-process reconciliation runs without an execution token.  A loaded-row
    check followed by ``update_status`` leaves a race where a dedicated worker
    can claim the job between those two operations.  Keep the old path usable,
    but make the status transition itself conditional on an absent/expired
    execution lease.
    """
    current = now or datetime.now()
    values = {"status": status, **kwargs}
    query = db.query(YoloTrainJob).filter(
        YoloTrainJob.id == int(job_id),
        or_(
            YoloTrainJob.execution_lease_owner.is_(None),
            YoloTrainJob.execution_lease_expires_at.is_(None),
            YoloTrainJob.execution_lease_expires_at <= current,
        ),
    )
    updated = query.update(values, synchronize_session=False)
    if int(updated or 0) != 1:
        return None
    # The caller may already have this row in the identity map from the
    # preceding stale-state scan; refresh it before propagating the status.
    db.expire_all()
    job = get(db, int(job_id))
    if job and job.training_task_id:
        training_task = (
            db.query(TrainingTask)
            .filter(
                TrainingTask.id == int(job.training_task_id),
                TrainingTask.latest_job_id == int(job.id),
            )
            .first()
        )
        if training_task is not None:
            training_task.latest_job_status = status
            db.add(training_task)
    db.flush()
    return job


def update_metrics_summary_if_current(
    db: Session,
    *,
    job_id: int,
    expected_metrics_summary: Optional[str],
    metrics_summary: str,
) -> bool:
    """Atomically persist an evaluation result only if its source is current.

    Test-set evaluation runs outside the request transaction.  Comparing the
    summary captured before inference makes the existing text column an
    optimistic version fence without a schema migration: a later evaluation
    or training result wins, and a stale evaluator cannot overwrite it.
    """
    query = db.query(YoloTrainJob).filter(
        YoloTrainJob.id == int(job_id),
        YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
    )
    if expected_metrics_summary is None:
        query = query.filter(YoloTrainJob.metrics_summary.is_(None))
    else:
        query = query.filter(YoloTrainJob.metrics_summary == expected_metrics_summary)
    updated = query.update(
        {YoloTrainJob.metrics_summary: metrics_summary},
        synchronize_session=False,
    )
    if int(updated or 0) != 1:
        return False
    db.flush()
    return True


def update_eval_status_if_current(
    db: Session,
    *,
    job_id: int,
    report_kind: str,
    expected: str | None,
    value: str,
) -> bool:
    """Conditionally claim or finish one asynchronous evaluation stage.

    The scheduler can run in more than one backend process.  An in-memory
    thread set is useful for local deduplication but cannot serialize two
    processes.  Use the existing status column as a small compare-and-set
    gate so only one process transitions an idle stage to ``RUNNING``.
    """
    if str(value).strip() == "RUNNING":
        return bool(
            claim_eval_status_if_current(
                db,
                job_id=job_id,
                report_kind=report_kind,
                expected=expected,
            )
        )
    normalized_kind = _normalize_eval_kind(report_kind)
    field_name = _eval_status_field_name(normalized_kind)
    field = getattr(YoloTrainJob, field_name)
    query = db.query(YoloTrainJob).filter(
        YoloTrainJob.id == int(job_id),
        YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
    )
    if expected is None:
        query = query.filter(field.is_(None))
    else:
        query = query.filter(field == str(expected))
    updated = query.update({field.key: str(value)}, synchronize_session=False)
    if int(updated or 0) != 1:
        return False
    db.flush()
    return True


def _normalize_eval_kind(report_kind: str) -> str:
    normalized = str(report_kind or "").strip()
    if normalized not in {"full_analysis", "next_iteration"}:
        raise ValueError(f"unsupported evaluation report kind: {report_kind!r}")
    return normalized


def _eval_status_field_name(report_kind: str) -> str:
    return (
        "ai_eval_full_status"
        if _normalize_eval_kind(report_kind) == "full_analysis"
        else "ai_eval_next_status"
    )


def _eval_operation_field_name(report_kind: str) -> str:
    return (
        "ai_eval_full_operation_id"
        if _normalize_eval_kind(report_kind) == "full_analysis"
        else "ai_eval_next_operation_id"
    )


def get_eval_operation_id(
    db: Session, *, job_id: int, report_kind: str
) -> str | None:
    """Read the current operation id; None is valid for historical rows."""
    field_name = _eval_operation_field_name(report_kind)
    row = db.query(getattr(YoloTrainJob, field_name)).filter(
        YoloTrainJob.id == int(job_id)
    ).first()
    if not row:
        return None
    value = getattr(row, field_name, None)
    return str(value).strip() if value else None


def claim_eval_status_if_current(
    db: Session,
    *,
    job_id: int,
    report_kind: str,
    expected: str | None,
    operation_id: str | None = None,
) -> str | None:
    """Claim one evaluation stage and issue a new execution generation.

    The status and operation id are written by one conditional UPDATE.  A
    process that loses the race gets ``None`` and must not call its provider.
    """
    field_name = _eval_status_field_name(report_kind)
    status_field = getattr(YoloTrainJob, field_name)
    operation_field_name = _eval_operation_field_name(report_kind)
    operation_field = getattr(YoloTrainJob, operation_field_name)
    issued = str(operation_id or "").strip() or uuid4().hex
    query = db.query(YoloTrainJob).filter(
        YoloTrainJob.id == int(job_id),
        YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
    )
    if expected is None:
        query = query.filter(status_field.is_(None))
    else:
        query = query.filter(status_field == str(expected))
    updated = query.update(
        {field_name: "RUNNING", operation_field_name: issued},
        synchronize_session=False,
    )
    if int(updated or 0) != 1:
        return None
    db.flush()
    return issued


def update_eval_status_for_operation(
    db: Session,
    *,
    job_id: int,
    report_kind: str,
    operation_id: str,
    value: str,
) -> bool:
    """Complete/fail only the currently owned evaluation generation."""
    normalized_operation = str(operation_id or "").strip()
    if not normalized_operation:
        return False
    status_field_name = _eval_status_field_name(report_kind)
    operation_field_name = _eval_operation_field_name(report_kind)
    updated = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.id == int(job_id),
            YoloTrainJob.status == YoloTrainStatus.COMPLETED.value,
            getattr(YoloTrainJob, status_field_name) == "RUNNING",
            getattr(YoloTrainJob, operation_field_name) == normalized_operation,
        )
        .update({status_field_name: str(value)}, synchronize_session=False)
    )
    if int(updated or 0) != 1:
        return False
    db.flush()
    return True


def reset_eval_status_for_operation(
    db: Session, *, job_id: int, report_kind: str, operation_id: str
) -> bool:
    """Release an orphaned generation without touching a newer generation."""
    normalized_operation = str(operation_id or "").strip()
    if not normalized_operation:
        return False
    status_field_name = _eval_status_field_name(report_kind)
    operation_field_name = _eval_operation_field_name(report_kind)
    updated = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.id == int(job_id),
            getattr(YoloTrainJob, status_field_name).in_(["RUNNING", "COMPLETED"]),
            getattr(YoloTrainJob, operation_field_name) == normalized_operation,
        )
        .update(
            {status_field_name: None, operation_field_name: None},
            synchronize_session=False,
        )
    )
    if int(updated or 0) != 1:
        return False
    db.flush()
    return True


def delete_row(db: Session, job: YoloTrainJob) -> None:
    db.delete(job)
    db.flush()
