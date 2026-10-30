import json
from typing import Any, List, Tuple
from sqlalchemy.exc import IntegrityError

from loguru import logger
from fastapi import status
from sqlalchemy import String, and_, case, cast, func, or_
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction
from labelu.internal.common.idempotency import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    normalize_idempotency_key,
    request_payload_hash,
)
from labelu.internal.common.config import settings
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.business_log import biz_log
from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.task_access import (
    is_consistent_pool_admin,
    is_task_superuser,
    user_has_task_access,
    user_is_task_owner_or_superuser,
)
from labelu.internal.common.system_dataset_category import (
    CONSISTENT_POOL_CATEGORY_KEY,
)
from labelu.internal.common.compare_sample_access_token import (
    verify_compare_sample_access_token,
)
from labelu.internal.common.label_image import label_rectangles
from labelu.internal.common.training_sample import is_explicit_negative_sample
from labelu.internal.common.yolo_detection_geometry import (
    count_detection_samples,
    detection_class_names,
    parse_task_config,
)
from labelu.internal.common.yolo_rect_class_slug import (
    build_rect_label_slug_aliases,
    resolve_rect_label_slug,
)
from labelu.internal.common.config import settings
from labelu.internal.common.plate_recognition_profile import (
    DEFAULT_PLATE_DETECTION_CONFIG,
    DEFAULT_PLATE_RECOGNITION_CONFIG,
    PLATE_DETECTION_PRESET_SLUG,
    PLATE_DETECTION_PROFILE,
    PLATE_RECOGNITION_PRESET_SLUG,
    PLATE_RECOGNITION_PROFILE,
    PLATE_PROFILE_NAMES,
    normalize_plate_recognition_task_config,
    parse_profile_config,
    profile_for_task_config,
    profile_preset_slug,
    task_config_has_plate_recognition_marker,
    task_config_matches_profile,
)
from labelu.internal.application.service import plate_recognition_train
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.task import MediaType
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task import TaskStatus
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationScope,
    SampleAnnotationLabelIndex,
    TaskAnnotationLabel,
)
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    DatasetCategory,
)
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.adapter.persistence import (
    crud_annotation_taxonomy,
    crud_task,
    crud_user,
)
from labelu.internal.adapter.persistence import crud_project
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.application.command.task import BasicConfigCommand
from labelu.internal.application.command.task import (
    AssignTaskProjectCommand,
    BatchAssignTaskProjectCommand,
)
from labelu.internal.application.command.task import BatchAssignDatasetCategoryCommand
from labelu.internal.application.command.task import UpdateCommand
from labelu.internal.application.response.base import UserResp
from labelu.internal.application.response.user import UserResponse
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.task import TaskStatics
from labelu.internal.application.response.task import TaskRectPresetSummaryItem
from labelu.internal.application.response.task import TaskRectPresetSummaryResponse
from labelu.internal.application.response.task import TaskImageSearchHit
from labelu.internal.application.response.task import TaskLabelImageHit
from labelu.internal.application.response.task import TaskTrainingCandidateResponse
from labelu.internal.application.response.task import TaskResponse
from labelu.internal.application.response.task import TaskResponseWithStatics
from labelu.internal.application.response.task import TaskAnnotationAuditSummary
from labelu.internal.application.response.task import TaskDatasetCategoryBatchResponse
from labelu.internal.application.response.task import TaskDatasetCategorySummaryItem
from labelu.internal.application.response.task import TaskDatasetCategorySummaryResponse
from labelu.internal.application.response.task import TaskProjectAssignmentResponse
from labelu.internal.application.service.attachment import build_attachment_response
from labelu.internal.application.service.annotation_taxonomy import (
    build_rect_label_presets_json,
    enrich_task_config_json_for_client,
)
from labelu.internal.application.service.task_training_ready import (
    ensure_task_training_ready_refreshed,
    training_ready_summary_for_response,
)
from labelu.internal.application.service.task_cleanup import (
    cleanup_task_records,
    remove_task_artifacts,
)
from labelu.internal.application.service.training_review_gate import (
    filter_trainable_samples,
)
from labelu.internal.common.task_annotation_completeness import (
    audit_one_sample,
    audit_task_config_and_samples,
)
from labelu.internal.application.service.yolo_dataset import _parse_sample_annotation


def _active_dataset_category(
    db: Session, dataset_category_id: int | None
) -> DatasetCategory | None:
    if dataset_category_id is None:
        return None
    category = (
        db.query(DatasetCategory)
        .filter(
            DatasetCategory.id == int(dataset_category_id),
            DatasetCategory.deleted_at.is_(None),
            DatasetCategory.status == "active",
        )
        .first()
    )
    if category is None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="数据集分类不存在或已停用",
        )
    return category


def _training_sample_stats(
    db: Session,
    *,
    task: Task,
    trainable_sample_count: int,
    done_sample_count: int,
    annotation_ready: bool | None = None,
) -> dict[str, Any]:
    """Build the task/sample admission state used by the training creator.

    ``Task.training_ready`` is intentionally not used as the only admission
    gate.  A task that is still being annotated can expose a qualified subset
    of DONE samples, while a FINISHED task can still contain samples rejected
    by the sample-level audit.
    """
    total_sample_count = int(
        db.query(func.count(TaskSample.id))
        .filter(
            TaskSample.task_id == int(task.id),
            TaskSample.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    skipped_count = int(
        db.query(func.count(TaskSample.id))
        .filter(
            TaskSample.task_id == int(task.id),
            TaskSample.deleted_at.is_(None),
            TaskSample.state == SampleState.SKIPPED.value,
        )
        .scalar()
        or 0
    )
    required_sample_count = max(total_sample_count - skipped_count, 0)
    done_sample_count = max(int(done_sample_count), 0)
    trainable_sample_count = max(min(int(trainable_sample_count), done_sample_count), 0)
    pending_sample_count = max(required_sample_count - done_sample_count, 0)
    excluded_sample_count = max(done_sample_count - trainable_sample_count, 0)
    completion_ratio = (
        round(done_sample_count / required_sample_count, 4)
        if required_sample_count
        else 0.0
    )
    quality_pass_ratio = (
        round(trainable_sample_count / done_sample_count, 4)
        if done_sample_count
        else 0.0
    )
    minimum_count = max(int(settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT or 1), 1)
    minimum_threshold_met = trainable_sample_count >= minimum_count
    # ``training_ready`` is the task-level annotation flag.  Keep it separate
    # from ``can_train``: an annotation can be complete while a media asset is
    # unavailable, in which case the task is correctly visible but cannot yet
    # be submitted for training.
    complete = (
        annotation_ready
        if annotation_ready is not None
        else (
            required_sample_count > 0
            and done_sample_count >= required_sample_count
            and trainable_sample_count >= required_sample_count
        )
    )
    can_train = bool(minimum_threshold_met)
    if complete and can_train:
        training_state = "COMPLETE_READY"
    elif can_train:
        training_state = "PARTIAL_READY"
    else:
        training_state = "UNAVAILABLE"
    block_reasons: list[str] = []
    if trainable_sample_count <= 0:
        block_reasons.append("NO_TRAINABLE_SAMPLES")
    if not minimum_threshold_met:
        block_reasons.append("MIN_SAMPLE_COUNT_NOT_MET")
    return {
        "training_ready": bool(complete),
        "can_train": can_train,
        "training_state": training_state,
        "task_status": str(getattr(task.status, "value", task.status) or ""),
        "total_sample_count": total_sample_count,
        "required_sample_count": required_sample_count,
        "done_count": done_sample_count,
        "done_sample_count": done_sample_count,
        "trainable_sample_count": trainable_sample_count,
        "pending_sample_count": pending_sample_count,
        "excluded_sample_count": excluded_sample_count,
        "completion_ratio": completion_ratio,
        "quality_pass_ratio": quality_pass_ratio,
        "minimum_threshold_met": minimum_threshold_met,
        "minimum_trainable_sample_count": minimum_count,
        "training_block_reasons": block_reasons,
    }


def _resolve_task_dataset_binding(
    db: Session,
    *,
    cmd: BasicConfigCommand,
    current_user: User,
) -> tuple[Dataset | None, DatasetLabelContract | None, str]:
    if cmd.dataset_id is None and cmd.dataset_label_contract_id is None:
        return None, None, (cmd.task_label_scope_mode or "FULL")
    dataset = None
    if cmd.dataset_id is not None:
        dataset = (
            db.query(Dataset)
            .filter(
                Dataset.id == int(cmd.dataset_id),
                Dataset.deleted_at.is_(None),
                Dataset.status == "ACTIVE",
            )
            .one_or_none()
        )
        if dataset is None:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="所选数据集不存在或已停用",
            )
        if not (
            is_task_superuser(current_user)
            or int(dataset.owner_id) == int(current_user.id)
            or db.query(Task.id)
            .outerjoin(TaskCollaborator, TaskCollaborator.task_id == Task.id)
            .filter(
                Task.dataset_id == int(dataset.id),
                Task.deleted_at.is_(None),
                or_(
                    Task.created_by == int(current_user.id),
                    TaskCollaborator.user_id == int(current_user.id),
                ),
            )
            .first()
            is not None
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=status.HTTP_403_FORBIDDEN,
                message="无权使用所选数据集",
            )
    contract = None
    contract_id = cmd.dataset_label_contract_id
    if contract_id is not None:
        contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == int(contract_id))
            .one_or_none()
        )
        if (
            contract is None
            or dataset is None
            or int(contract.dataset_id) != int(dataset.id)
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="数据集标签契约与数据集不匹配",
            )
    elif dataset is not None and getattr(dataset, "dataset_label_contract_id", None):
        contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == int(dataset.dataset_label_contract_id))
            .one_or_none()
        )
    if (
        dataset is not None
        and cmd.dataset_category_id is not None
        and int(dataset.dataset_category_id or 0) != int(cmd.dataset_category_id)
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="任务分类必须与所选数据集一致",
        )
    scope = str(cmd.task_label_scope_mode or "FULL").upper()
    if scope not in {"FULL", "SUBSET"}:
        scope = "FULL"
    return dataset, contract, scope


def _dataset_category_fields(task: Task) -> dict:
    category = task.dataset_category
    return {
        "dataset_category_id": int(category.id) if category else None,
        "dataset_category_key": category.category_key if category else None,
        "dataset_category_name": category.name if category else None,
        "dataset_category_status": category.status if category else None,
    }


async def create(
    db: Session,
    cmd: BasicConfigCommand,
    current_user: User,
    *,
    idempotency_key: str | None = None,
) -> TaskResponse:
    # Optional client retry protection. Omitting the header preserves the
    # historical behavior that intentionally allows two distinct tasks with
    # the same visible configuration.
    retry_key = normalize_idempotency_key(idempotency_key)
    if retry_key and len(retry_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="Idempotency-Key 不能超过 256 个字符",
        )
    retry_hash = request_payload_hash(cmd.dict()) if retry_key else None
    if retry_key:
        existing = crud_task.get_by_idempotency(
            db, creator_id=current_user.id, idempotency_key=retry_key
        )
        if existing is not None:
            if existing.idempotency_request_hash != retry_hash:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message="Idempotency-Key 已用于其他任务请求",
                )
            return _task_response_for_create(existing)

    category = _active_dataset_category(db, cmd.dataset_category_id)

    with begin_transaction(db):
        dataset, dataset_contract, scope_mode = _resolve_task_dataset_binding(
            db, cmd=cmd, current_user=current_user
        )
        new_task = None
        try:
            with db.begin_nested():
                new_task = crud_task.create(
                    db=db,
                    task=Task(
                        status=TaskStatus.DRAFT.value,
                        name=cmd.name,
                        description=cmd.description,
                        media_type=cmd.media_type,
                        tips=cmd.tips,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        idempotency_key=retry_key,
                        idempotency_request_hash=retry_hash,
                        dataset_category_id=(
                            int(category.id)
                            if category
                            else (
                                int(dataset.dataset_category_id)
                                if dataset and dataset.dataset_category_id is not None
                                else None
                            )
                        ),
                        dataset_id=(int(dataset.id) if dataset else None),
                        dataset_label_contract_id=(
                            int(dataset_contract.id) if dataset_contract else None
                        ),
                        task_label_scope_mode=scope_mode,
                    ),
                )
        except IntegrityError:
            if retry_key:
                existing = crud_task.get_by_idempotency(
                    db, creator_id=current_user.id, idempotency_key=retry_key
                )
                if existing is not None:
                    if existing.idempotency_request_hash != retry_hash:
                        raise LabelUException(
                            code=ErrorCode.CODE_30003_CLIENT_ERROR,
                            status_code=status.HTTP_409_CONFLICT,
                            message="Idempotency-Key 已用于其他任务请求",
                        )
                    new_task = existing
            if new_task is None:
                raise
        if (
            settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
            and new_task is not None
            and new_task.idempotency_key == retry_key
        ):
            enqueue_auto_training_event(
                db,
                event_name="task.created.v1",
                aggregate_type="task",
                aggregate_id=str(new_task.id),
                payload={
                    "task_id": int(new_task.id),
                    "dataset_category_id": new_task.dataset_category_id,
                    "media_type": new_task.media_type,
                },
                idempotency_key=f"task:{new_task.id}:created",
            )

    return _task_response_for_create(new_task)


def _task_response_for_create(new_task: Task) -> TaskResponse:
    """Build the stable task response for initial and idempotent replay paths."""
    return TaskResponse(
        id=new_task.id,
        project_id=new_task.project_id,
        name=new_task.name,
        description=new_task.description,
        tips=new_task.tips,
        status=new_task.status,
        media_type=new_task.media_type,
        created_at=new_task.created_at,
        created_by=UserResp(
            id=new_task.owner.id,
            username=new_task.owner.username,
        ),
        source_type=str(new_task.source_type or "internal"),
        source_system=new_task.source_system,
        source_external_task_id=new_task.source_external_task_id,
        source_external_package_id=new_task.source_external_package_id,
        source_integration_id=new_task.source_integration_id,
        dataset_id=getattr(new_task, "dataset_id", None),
        dataset_label_contract_id=getattr(new_task, "dataset_label_contract_id", None),
        task_label_scope_mode=str(
            getattr(new_task, "task_label_scope_mode", "FULL") or "FULL"
        ),
        **_dataset_category_fields(new_task),
    )


def _task_response_with_statics(
    db: Session,
    task: Task,
    statics: dict,
    *,
    allow_status_transition: bool = True,
    contract=None,
    resolve_contract: bool = True,
    global_dimensions=None,
    rect_label_presets: list[dict] | None = None,
    enrich_config: bool = True,
) -> TaskResponseWithStatics:
    from labelu.internal.application.service import (
        annotation_contract as contract_service,
    )

    # Detail responses still resolve their own contract.  List responses pass
    # a preloaded map so one page does not issue one contract query per card.
    if resolve_contract and contract is None:
        contract = contract_service.current_contract(db, int(task.id))
    if task.media_type == MediaType.IMAGE.value and task.training_ready is None:
        ensure_task_training_ready_refreshed(
            db,
            task,
            allow_status_transition=allow_status_transition,
        )
    ready = training_ready_summary_for_response(task)
    audit_raw = ready.get("annotation_audit")
    audit = (
        TaskAnnotationAuditSummary(**audit_raw) if isinstance(audit_raw, dict) else None
    )
    return TaskResponseWithStatics(
        id=task.id,
        project_id=task.project_id,
        name=task.name,
        description=task.description,
        tips=task.tips,
        media_type=task.media_type,
        config=(
            enrich_task_config_json_for_client(
                db,
                task.config,
                task.media_type,
                global_dimensions=global_dimensions,
                rect_label_presets=rect_label_presets,
            )
            if enrich_config
            else task.config
        ),
        status=task.status,
        created_at=task.created_at,
        created_by=UserResp(
            id=task.owner.id,
            username=task.owner.username,
        ),
        source_type=str(task.source_type or "internal"),
        source_system=task.source_system,
        source_external_task_id=task.source_external_task_id,
        source_external_package_id=task.source_external_package_id,
        source_integration_id=task.source_integration_id,
        **_dataset_category_fields(task),
        dataset_id=getattr(task, "dataset_id", None),
        dataset_label_contract_id=getattr(task, "dataset_label_contract_id", None),
        task_label_scope_mode=str(
            getattr(task, "task_label_scope_mode", "FULL") or "FULL"
        ),
        current_annotation_contract_id=(contract.id if contract else None),
        annotation_contract_revision=(contract.revision if contract else None),
        label_set_signature=(contract.label_set_signature if contract else None),
        class_order_signature=(contract.class_order_signature if contract else None),
        stats=TaskStatics(
            new=statics.get(f"{task.id}_{SampleState.NEW.value}", 0),
            done=statics.get(f"{task.id}_{SampleState.DONE.value}", 0),
            skipped=statics.get(f"{task.id}_{SampleState.SKIPPED.value}", 0),
            abnormal=statics.get(f"{task.id}_{SampleState.ABNORMAL.value}", 0),
            incomplete=statics.get(f"{task.id}_{SampleState.INCOMPLETE.value}", 0),
        ),
        training_ready=ready.get("training_ready"),
        annotation_audit=audit,
    )


def _is_train_filtered_consistent_pool(task: Task) -> bool:
    cfg = _parse_task_config(task.config)
    pool = (
        cfg.get("consistent_pool")
        if isinstance(cfg.get("consistent_pool"), dict)
        else {}
    )
    return pool.get("kind") == "train_filtered_consistent"


def _task_config_json_text_expr(db: Session, path: str):
    bind = db.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") if bind else ""
    valid_json = func.json_valid(Task.config)
    if dialect_name.startswith("sqlite"):
        return case(
            (valid_json == 1, func.json_extract(Task.config, path)),
            else_=None,
        )
    if dialect_name.startswith("mysql"):
        return case(
            (valid_json == 1, func.json_unquote(func.json_extract(Task.config, path))),
            else_=None,
        )
    return None


def _list_training_candidate_prefiltered_tasks(
    db: Session,
    current_user: User,
    *,
    rect_preset_slug: str,
    preset_classes_by_slug: dict[str, list[str]],
) -> list[Task]:
    query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(
        Task.status == TaskStatus.FINISHED.value,
        Task.media_type == MediaType.IMAGE.value,
        or_(Task.training_ready.is_(True), Task.training_ready.is_(None)),
    )

    consistent_pool_kind_expr = _task_config_json_text_expr(
        db, "$.consistent_pool.kind"
    )
    if consistent_pool_kind_expr is not None:
        query = query.filter(
            or_(
                consistent_pool_kind_expr.is_(None),
                consistent_pool_kind_expr != "train_filtered_consistent",
            )
        )

    applied_slug_expr = _task_config_json_text_expr(db, "$.defaultRectPresetId")
    if applied_slug_expr is not None:
        candidate_filters = [
            applied_slug_expr == rect_preset_slug,
            applied_slug_expr.is_(None),
            applied_slug_expr == "",
        ]
        known_slugs = [slug for slug in preset_classes_by_slug.keys() if slug]
        if known_slugs:
            candidate_filters.append(applied_slug_expr.notin_(known_slugs))
        query = query.filter(or_(*candidate_filters))

    return query.order_by(Task.id.desc()).all()


def _list_rect_preset_selectable_tasks(
    db: Session,
    current_user: User,
    *,
    rect_preset_slug: str,
    preset_classes_by_slug: dict[str, list[str]],
    search: str | None = None,
) -> list[Task]:
    query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(Task.media_type == MediaType.IMAGE.value)
    query = _filter_training_candidate_search(query, search)

    tasks = query.order_by(Task.id.desc()).all()
    matched: list[Task] = []
    for task in tasks:
        if not _task_matches_rect_preset_filter(
            task,
            rect_preset_slug,
            preset_classes_by_slug,
        ):
            continue
        if _is_train_filtered_consistent_pool(task):
            if task.status != TaskStatus.DRAFT.value:
                matched.append(task)
            continue
        if task.status == TaskStatus.FINISHED.value:
            matched.append(task)
    return matched


def list_by(
    db: Session,
    current_user: User,
    page: int,
    size: int,
    *,
    status: str | None = None,
    media_type: str | None = None,
    rect_preset_slug: str | None = None,
    label_ids: list[int] | None = None,
    training_ready: bool | None = None,
    dataset_category_id: int | None = None,
    uncategorized: bool = False,
    project_id: int | None = None,
    dataset_id: int | None = None,
    dataset_label_contract_id: int | None = None,
    dataset_search: str | None = None,
    search: str | None = None,
) -> Tuple[List[TaskResponseWithStatics], int]:
    rect_preset_slug = (rect_preset_slug or "").strip()
    normalized_label_ids = list(
        dict.fromkeys(int(value) for value in (label_ids or []))
    )

    def apply_dataset_filter(query):
        if dataset_id is not None:
            query = query.filter(Task.dataset_id == int(dataset_id))
        if dataset_label_contract_id is not None:
            query = query.filter(
                (Task.dataset_label_contract_id == int(dataset_label_contract_id))
                | (
                    db.query(Dataset.id)
                    .filter(
                        Dataset.id == Task.dataset_id,
                        Dataset.dataset_label_contract_id
                        == int(dataset_label_contract_id),
                    )
                    .exists()
                )
            )
        keyword = str(dataset_search or "").strip()
        if keyword:
            pattern = literal_like_pattern(keyword)
            query = query.filter(
                db.query(Dataset.id)
                .outerjoin(
                    DatasetLabelContract,
                    DatasetLabelContract.id == Dataset.dataset_label_contract_id,
                )
                .filter(
                    Dataset.id == Task.dataset_id,
                    or_(
                        Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                        Dataset.description.ilike(pattern, escape=LIKE_ESCAPE),
                        DatasetLabelContract.labels_snapshot_json.ilike(
                            pattern, escape=LIKE_ESCAPE
                        ),
                    ),
                )
                .exists()
            )
        general_keyword = str(search or "").strip()
        if general_keyword:
            pattern = literal_like_pattern(general_keyword)
            dataset_match = (
                db.query(Dataset.id)
                .outerjoin(
                    DatasetLabelContract,
                    DatasetLabelContract.id == Dataset.dataset_label_contract_id,
                )
                .filter(
                    Dataset.id == Task.dataset_id,
                    or_(
                        Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                        Dataset.description.ilike(pattern, escape=LIKE_ESCAPE),
                        DatasetLabelContract.labels_snapshot_json.ilike(
                            pattern, escape=LIKE_ESCAPE
                        ),
                    ),
                )
                .exists()
            )
            query = query.filter(
                or_(
                    cast(Task.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                    Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.description.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.tips.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.status.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.media_type.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.source_type.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.source_system.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.source_external_task_id.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.source_external_package_id.ilike(pattern, escape=LIKE_ESCAPE),
                    cast(Task.dataset_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                    dataset_match,
                )
            )
        return query

    # get task list
    if normalized_label_ids:
        query = (
            crud_task.visible_query(
                db=db,
                owner_id=current_user.id,
                list_all_tasks=is_task_superuser(current_user),
            )
            .join(
                TaskAnnotationLabel,
                and_(
                    TaskAnnotationLabel.task_id == Task.id,
                    TaskAnnotationLabel.contract_id
                    == Task.current_annotation_contract_id,
                    TaskAnnotationLabel.status == "active",
                ),
            )
            .filter(TaskAnnotationLabel.label_id.in_(normalized_label_ids))
        )
        if status and str(status).strip():
            query = query.filter(Task.status == str(status).strip())
        if media_type and str(media_type).strip():
            query = query.filter(Task.media_type == str(media_type).strip())
        if training_ready is not None:
            query = query.filter(Task.training_ready.is_(bool(training_ready)))
        if dataset_category_id is not None:
            query = query.filter(Task.dataset_category_id == int(dataset_category_id))
        elif uncategorized:
            query = query.filter(Task.dataset_category_id.is_(None))
        if project_id is not None:
            query = query.filter(Task.project_id == int(project_id))
        query = apply_dataset_filter(query)
        query = query.group_by(Task.id).having(
            func.count(func.distinct(TaskAnnotationLabel.label_id))
            == len(normalized_label_ids)
        )
        total_task = query.count()
        tasks = (
            query.options(joinedload(Task.owner))
            .order_by(Task.id.desc())
            .offset(page * size)
            .limit(size)
            .all()
        )
    elif rect_preset_slug:
        _, preset_classes_by_slug = _load_preset_classes_by_slug(db)
        query = crud_task.visible_query(
            db=db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        if status and str(status).strip():
            query = query.filter(Task.status == str(status).strip())
        if media_type and str(media_type).strip():
            query = query.filter(Task.media_type == str(media_type).strip())
        if dataset_category_id is not None:
            query = query.filter(Task.dataset_category_id == int(dataset_category_id))
        elif uncategorized:
            query = query.filter(Task.dataset_category_id.is_(None))
        if project_id is not None:
            query = query.filter(Task.project_id == int(project_id))
        query = apply_dataset_filter(query)
        # training_ready 在 Python 层过滤，以便「训练筛选一致」池任务仍随预设 Tab 展示
        fetched = query.options(joinedload(Task.owner)).order_by(Task.id.desc()).all()
        if training_ready is True:
            tasks_before_filter = [
                t
                for t in fetched
                if t.training_ready is True or _is_train_filtered_consistent_pool(t)
            ]
        elif training_ready is False:
            tasks_before_filter = [
                t
                for t in fetched
                if t.training_ready is False
                and not _is_train_filtered_consistent_pool(t)
            ]
        else:
            tasks_before_filter = fetched
        all_matching = [
            task
            for task in tasks_before_filter
            if _task_matches_rect_preset_filter(
                task,
                rect_preset_slug,
                preset_classes_by_slug,
            )
        ]
        total_task = len(all_matching)
        tasks = all_matching[page * size : page * size + size]
    else:
        query = crud_task.visible_query(
            db=db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        if status and str(status).strip():
            query = query.filter(Task.status == str(status).strip())
        if media_type and str(media_type).strip():
            query = query.filter(Task.media_type == str(media_type).strip())
        if training_ready is not None:
            query = query.filter(Task.training_ready.is_(bool(training_ready)))
        if dataset_category_id is not None:
            query = query.filter(Task.dataset_category_id == int(dataset_category_id))
        elif uncategorized:
            query = query.filter(Task.dataset_category_id.is_(None))
        if project_id is not None:
            query = query.filter(Task.project_id == int(project_id))
        query = apply_dataset_filter(query)
        total_task = query.count()
        tasks = (
            query.options(joinedload(Task.owner))
            .order_by(Task.id.desc())
            .offset(page * size)
            .limit(size)
            .all()
        )

    # get progress
    task_ids = [task.id for task in tasks]
    statics = crud_sample.statics(db=db, task_ids=task_ids)

    # The task-list endpoint is the first screen users see and commonly
    # contains 16 cards.  Resolve shared taxonomy/contract data once for the
    # whole page instead of repeating the same queries for every card.
    from labelu.internal.application.service import (
        annotation_contract as contract_service,
    )

    contracts = contract_service.current_contracts_for_tasks(db, tasks)
    global_dimensions = crud_annotation_taxonomy.global_dimensions_all_active(db)
    preset_rows, _ = crud_annotation_taxonomy.label_preset_list(db, page=0, size=500)
    rect_label_presets = build_rect_label_presets_json(db, preset_rows=preset_rows)

    # response
    tasks_with_statics = [
        # A filtered GET must not change status and then return a row that no
        # longer matches its own status filter.
        _task_response_with_statics(
            db,
            task,
            statics,
            allow_status_transition=False,
            contract=contracts.get(int(task.id)),
            resolve_contract=False,
            global_dimensions=global_dimensions,
            rect_label_presets=rect_label_presets,
        )
        for task in tasks
    ]
    return tasks_with_statics, total_task


def label_summary(
    db: Session,
    current_user: User,
    *,
    dataset_category_id: int | None = None,
    dataset_id: int | None = None,
    uncategorized: bool = False,
) -> dict:
    visible_query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(Task.media_type == MediaType.IMAGE.value)
    if dataset_id is not None:
        visible_query = visible_query.filter(Task.dataset_id == int(dataset_id))
    elif dataset_category_id is not None:
        visible_query = visible_query.filter(
            Task.dataset_category_id == int(dataset_category_id)
        )
    elif uncategorized:
        visible_query = visible_query.filter(Task.dataset_category_id.is_(None))
    visible_task_ids = {
        int(row[0]) for row in visible_query.with_entities(Task.id).all()
    }
    labels = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.deleted_at.is_(None),
        )
        .order_by(AnnotationLabel.sort_order, AnnotationLabel.id)
        .all()
    )
    counts: dict[int, int] = {}
    if visible_task_ids:
        count_rows = (
            db.query(
                AnnotationLabel.id,
                func.count(func.distinct(SampleAnnotationLabelIndex.sample_id)),
            )
            .select_from(SampleAnnotationLabelIndex)
            .join(TaskSample, TaskSample.id == SampleAnnotationLabelIndex.sample_id)
            .join(Task, Task.id == SampleAnnotationLabelIndex.task_id)
            .join(
                TaskAnnotationLabel,
                and_(
                    TaskAnnotationLabel.task_id == SampleAnnotationLabelIndex.task_id,
                    TaskAnnotationLabel.contract_id
                    == Task.current_annotation_contract_id,
                    TaskAnnotationLabel.status == "active",
                ),
            )
            .join(
                AnnotationLabel,
                and_(
                    AnnotationLabel.id == TaskAnnotationLabel.label_id,
                    AnnotationLabel.deleted_at.is_(None),
                    or_(
                        AnnotationLabel.label_value
                        == SampleAnnotationLabelIndex.label_value,
                        AnnotationLabel.label_key
                        == SampleAnnotationLabelIndex.label_value,
                    ),
                ),
            )
            .filter(
                SampleAnnotationLabelIndex.task_id.in_(visible_task_ids),
                TaskSample.deleted_at.is_(None),
            )
            .group_by(AnnotationLabel.id)
            .all()
        )
        counts = {int(label_id): int(count) for label_id, count in count_rows}

    labels.sort(
        key=lambda row: (
            -int(counts.get(int(row.id), 0) or 0),
            int(row.sort_order or 0),
            int(row.id),
        )
    )
    return {
        "all": len(visible_task_ids),
        "labels": [
            {
                "id": int(row.id),
                "label_key": row.label_key,
                "label_value": row.label_value,
                "color": row.color,
                "count": int(counts.get(int(row.id), 0) or 0),
            }
            for row in labels
        ],
    }


def label_images(
    db: Session,
    current_user: User,
    *,
    label_id: int,
    page: int,
    size: int,
    search: str | None = None,
    dataset_category_id: int | None = None,
    dataset_id: int | None = None,
    uncategorized: bool = False,
) -> tuple[list[TaskLabelImageHit], int, AnnotationLabel]:
    label = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.id == int(label_id), AnnotationLabel.deleted_at.is_(None)
        )
        .first()
    )
    if not label:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="标签不存在",
        )

    task_query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(Task.media_type == MediaType.IMAGE.value)
    if dataset_id is not None:
        task_query = task_query.filter(Task.dataset_id == int(dataset_id))
    elif dataset_category_id is not None:
        task_query = task_query.filter(
            Task.dataset_category_id == int(dataset_category_id)
        )
    elif uncategorized:
        task_query = task_query.filter(Task.dataset_category_id.is_(None))
    task_ids = [int(row[0]) for row in task_query.with_entities(Task.id).all()]
    if not task_ids:
        return [], 0, label

    label_values = {
        str(value).strip() for value in (label.label_value, label.label_key) if value
    }
    matching_sample_ids = (
        db.query(TaskSample.id)
        .select_from(SampleAnnotationLabelIndex)
        .join(TaskSample, TaskSample.id == SampleAnnotationLabelIndex.sample_id)
        .join(Task, Task.id == SampleAnnotationLabelIndex.task_id)
        .join(TaskAttachment, TaskAttachment.id == TaskSample.file_id)
        .join(
            TaskAnnotationLabel,
            and_(
                TaskAnnotationLabel.task_id == SampleAnnotationLabelIndex.task_id,
                TaskAnnotationLabel.contract_id == Task.current_annotation_contract_id,
                TaskAnnotationLabel.label_id == int(label_id),
                TaskAnnotationLabel.status == "active",
            ),
        )
        .filter(
            SampleAnnotationLabelIndex.task_id.in_(task_ids),
            SampleAnnotationLabelIndex.label_value.in_(label_values),
            TaskSample.deleted_at.is_(None),
            TaskAttachment.deleted_at.is_(None),
        )
        .distinct()
        .order_by(TaskSample.id.asc())
    )
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        matching_sample_ids = matching_sample_ids.filter(
            or_(
                TaskAttachment.filename.ilike(pattern, escape=LIKE_ESCAPE),
                Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                cast(TaskSample.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(Task.id, String).ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = matching_sample_ids.count()
    page_size = max(int(size), 1)
    sample_id_rows = (
        matching_sample_ids.offset(max(int(page), 0) * page_size).limit(page_size).all()
    )
    sample_ids = [int(row[0]) for row in sample_id_rows]
    samples_by_id = {
        int(sample.id): sample
        for sample in db.query(TaskSample)
        .options(joinedload(TaskSample.file), joinedload(TaskSample.task))
        .filter(TaskSample.id.in_(sample_ids))
        .all()
    }
    hits: list[TaskLabelImageHit] = []
    for sample_id in sample_ids:
        sample = samples_by_id.get(sample_id)
        if sample is None:
            continue
        result, matches = label_rectangles(sample.data, label_values)
        if not matches or not sample.file or not sample.task:
            continue
        file_resp = build_attachment_response(sample.file)
        annotations = []
        for item in matches:
            annotation = dict(item)
            annotation["color"] = label.color
            annotations.append(annotation)
        hits.append(
            TaskLabelImageHit(
                sample_id=int(sample.id),
                task_id=int(sample.task_id),
                task_name=sample.task.name or "",
                filename=sample.file.filename or "",
                state=sample.state,
                url=getattr(file_resp, "url", None),
                thumbnail_url=getattr(file_resp, "thumbnail_url", None),
                width=result.get("width"),
                height=result.get("height"),
                annotations=annotations,
            )
        )
    return hits, total, label


def dataset_category_summary(
    db: Session, current_user: User
) -> TaskDatasetCategorySummaryResponse:
    visible = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    )
    counts = dict(
        visible.with_entities(Task.dataset_category_id, func.count(Task.id))
        .group_by(Task.dataset_category_id)
        .all()
    )
    category_query = db.query(DatasetCategory).filter(
        DatasetCategory.deleted_at.is_(None)
    )
    if not is_consistent_pool_admin(current_user):
        category_query = category_query.filter(
            DatasetCategory.category_key != CONSISTENT_POOL_CATEGORY_KEY
        )
    categories = category_query.order_by(
        DatasetCategory.sort_order, DatasetCategory.id
    ).all()
    all_count = sum(int(value or 0) for value in counts.values())
    uncategorized = int(counts.get(None, 0) or 0)
    return TaskDatasetCategorySummaryResponse(
        all=all_count,
        categorized=all_count - uncategorized,
        uncategorized=uncategorized,
        categories=[
            TaskDatasetCategorySummaryItem(
                id=int(row.id),
                category_key=row.category_key,
                name=row.name,
                status=row.status,
                count=int(counts.get(row.id, 0) or 0),
            )
            for row in categories
        ],
    )


def _training_task_source_ids(row: TrainingTask) -> set[int]:
    ids = {int(row.primary_task_id)} if row.primary_task_id else set()
    try:
        payload = json.loads(row.source_tasks_snapshot_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = []
    for item in payload if isinstance(payload, list) else []:
        if isinstance(item, dict) and item.get("task_id"):
            ids.add(int(item["task_id"]))
    return ids


def _sync_unclassified_downstream(
    db: Session,
    *,
    task_ids: list[int],
    dataset_category: DatasetCategory,
) -> tuple[int, int, int]:
    selected_ids = {int(value) for value in task_ids}
    category_id = int(dataset_category.id)
    category_snapshot = json.dumps(
        {
            "id": category_id,
            "category_key": dataset_category.category_key,
            "name": dataset_category.name,
        },
        ensure_ascii=False,
    )
    synced_training_ids: list[int] = []
    candidates = (
        db.query(TrainingTask).filter(TrainingTask.dataset_category_id.is_(None)).all()
    )
    for row in candidates:
        source_ids = _training_task_source_ids(row)
        if not source_ids or not source_ids.intersection(selected_ids):
            continue
        source_categories = {
            value
            for (value,) in db.query(Task.dataset_category_id)
            .filter(Task.id.in_(source_ids), Task.deleted_at.is_(None))
            .all()
        }
        if source_categories != {category_id}:
            continue
        row.dataset_category_id = category_id
        row.dataset_category_snapshot_json = category_snapshot
        db.add(row)
        synced_training_ids.append(int(row.id))

    job_query = db.query(YoloTrainJob).filter(
        YoloTrainJob.dataset_category_id.is_(None)
    )
    conditions = [YoloTrainJob.task_id.in_(selected_ids)]
    if synced_training_ids:
        conditions.append(YoloTrainJob.training_task_id.in_(synced_training_ids))
    synced_jobs = job_query.filter(or_(*conditions)).update(
        {YoloTrainJob.dataset_category_id: category_id},
        synchronize_session=False,
    )
    synced_integrations = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.labelu_task_id.in_(selected_ids),
            StrategyTaskIntegration.dataset_category_id.is_(None),
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .update(
            {StrategyTaskIntegration.dataset_category_id: category_id},
            synchronize_session=False,
        )
    )
    return (
        len(synced_training_ids),
        int(synced_jobs or 0),
        int(synced_integrations or 0),
    )


def assign_dataset_category(
    db: Session,
    *,
    cmd: BatchAssignDatasetCategoryCommand,
    current_user: User,
) -> TaskDatasetCategoryBatchResponse:
    category = _active_dataset_category(db, cmd.dataset_category_id)
    rows = (
        db.query(Task)
        .filter(Task.id.in_(cmd.task_ids), Task.deleted_at.is_(None))
        .all()
    )
    rows_by_id = {int(row.id): row for row in rows}
    missing = [task_id for task_id in cmd.task_ids if task_id not in rows_by_id]
    if missing:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"任务不存在: {missing}",
        )
    denied = [
        task_id
        for task_id, row in rows_by_id.items()
        if not user_is_task_owner_or_superuser(current_user, row)
    ]
    if denied:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message=f"无权修改任务分类: {sorted(denied)}",
        )

    synced_training = synced_jobs = synced_integrations = 0
    with begin_transaction(db):
        for row in rows:
            row.dataset_category_id = int(category.id) if category else None
            row.updated_by = current_user.id
            db.add(row)
        db.flush()
        if category is not None and cmd.sync_unclassified_downstream:
            synced_training, synced_jobs, synced_integrations = (
                _sync_unclassified_downstream(
                    db,
                    task_ids=cmd.task_ids,
                    dataset_category=category,
                )
            )
        db.flush()

    return TaskDatasetCategoryBatchResponse(
        task_ids=cmd.task_ids,
        dataset_category_id=(int(category.id) if category else None),
        updated_task_count=len(rows),
        synced_training_task_count=synced_training,
        synced_train_job_count=synced_jobs,
        synced_integration_count=synced_integrations,
    )


def _filter_training_candidate_search(query, search: str | None):
    """Apply the create-training search without weakening visibility filters.

    Candidate eligibility still comes from the existing per-task audit.  This
    predicate only narrows the visible task set before that audit, which keeps
    search useful for large task/dataset catalogs and avoids exposing rows
    outside the caller's access scope.
    """
    keyword = str(search or "").strip()
    if not keyword:
        return query
    pattern = literal_like_pattern(keyword)
    return query.outerjoin(Dataset, Dataset.id == Task.dataset_id).filter(
        or_(
            Task.name.ilike(pattern, escape=LIKE_ESCAPE),
            Task.description.ilike(pattern, escape=LIKE_ESCAPE),
            Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
        )
    )


async def list_training_candidates_by_labels(
    db: Session,
    current_user: User,
    *,
    label_ids: list[int],
    page: int,
    size: int,
    project_id: int | None = None,
    dataset_id: int | None = None,
    dataset_ids: list[int] | None = None,
    dataset_category_ids: list[int] | None = None,
    search: str | None = None,
) -> Tuple[List[TaskTrainingCandidateResponse], int]:
    from labelu.internal.application.service import annotation_contract

    labels = annotation_contract.active_labels_by_ids(db, label_ids)
    requested_ids = [int(row.id) for row in labels]
    requested_values = {str(row.label_value) for row in labels}
    requested_set = set(requested_ids)
    query = (
        crud_task.visible_query(
            db=db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        .join(
            TaskAnnotationLabel,
            and_(
                TaskAnnotationLabel.task_id == Task.id,
                TaskAnnotationLabel.contract_id == Task.current_annotation_contract_id,
                TaskAnnotationLabel.status == "active",
            ),
        )
        .filter(
            Task.media_type == MediaType.IMAGE.value,
            TaskAnnotationLabel.label_id.in_(requested_ids),
        )
        .group_by(Task.id)
        .having(
            func.count(func.distinct(TaskAnnotationLabel.label_id))
            == len(requested_ids)
        )
        .order_by(Task.id.desc())
    )
    if project_id is not None:
        query = query.filter(Task.project_id == int(project_id))
    scoped_dataset_ids = [int(value) for value in (dataset_ids or []) if int(value) > 0]
    if dataset_id is not None and int(dataset_id) not in scoped_dataset_ids:
        scoped_dataset_ids.insert(0, int(dataset_id))
    if scoped_dataset_ids:
        query = query.filter(Task.dataset_id.in_(scoped_dataset_ids))
    if dataset_category_ids:
        query = query.filter(
            Task.dataset_category_id.in_([int(value) for value in dataset_category_ids])
        )
    query = _filter_training_candidate_search(query, search)
    matched: list[TaskTrainingCandidateResponse] = []
    for task in query.all():
        contract = annotation_contract.current_contract(db, int(task.id))
        if contract is None or str(contract.status or "") not in (
            annotation_contract.CURRENT_CONTRACT_TRAINING_STATUSES
        ):
            continue
        samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == task.id,
                TaskSample.state == SampleState.DONE.value,
                TaskSample.deleted_at.is_(None),
            )
            .all()
        )
        done_sample_count = len(samples)
        annotation_audit = audit_task_config_and_samples(
            task.config, crud_sample.list_by_task(db=db, task_id=int(task.id))
        )
        samples = [
            sample
            for sample in filter_trainable_samples(db, task=task, samples=samples)
            if getattr(sample, "file", None) is not None
        ]
        usable = 0
        for sample in samples:
            try:
                outer = json.loads(sample.data or "{}")
                result = json.loads(outer.get("result") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            rects = (result.get("rectTool") or {}).get("result") or []
            if any(
                str((rect or {}).get("label") or "").strip() in requested_values
                for rect in rects
            ):
                usable += 1
                continue
            if is_explicit_negative_sample(outer) and requested_set.issubset(
                set(json.loads(contract.label_ids_json or "[]"))
            ):
                usable += 1
        sample_stats = _training_sample_stats(
            db,
            task=task,
            trainable_sample_count=usable,
            done_sample_count=done_sample_count,
            annotation_ready=bool(annotation_audit.get("training_ready")),
        )
        matched.append(
            TaskTrainingCandidateResponse(
                task_id=task.id,
                task_name=task.name,
                **sample_stats,
                created_at=task.created_at,
                resolved_rect_preset_slug="",
                match_source="label_contract",
                contract_status=str(contract.status or ""),
                dataset_category_id=task.dataset_category_id,
                dataset_category_name=(
                    task.dataset_category.name if task.dataset_category else None
                ),
                dataset_id=getattr(task, "dataset_id", None),
                dataset_label_contract_id=getattr(
                    task, "dataset_label_contract_id", None
                ),
                task_label_scope_mode=str(
                    getattr(task, "task_label_scope_mode", "FULL") or "FULL"
                ),
            )
        )
    total = len(matched)
    return matched[page * size : page * size + size], total


async def list_task_dimension_training_candidates(
    db: Session,
    current_user: User,
    *,
    page: int,
    size: int,
    project_id: int | None = None,
    dataset_id: int | None = None,
    dataset_ids: list[int] | None = None,
    dataset_category_ids: list[int] | None = None,
    label_ids: list[int] | None = None,
    training_recipe_key: str | None = None,
    search: str | None = None,
) -> Tuple[List[TaskTrainingCandidateResponse], int]:
    """Return image tasks that have a trainable contract and usable samples.

    A few invalid or incomplete samples must not hide the valid remainder of a
    dataset from training. Preview and dataset freezing perform the definitive
    per-sample filtering before a job is created.
    """
    from labelu.internal.application.service import annotation_contract

    requested_label_ids: set[int] = set()
    if label_ids:
        try:
            requested_label_ids = {int(value) for value in label_ids}
        except (TypeError, ValueError) as exc:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="任务标签筛选参数无效",
            ) from exc
        if any(value <= 0 for value in requested_label_ids):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="任务标签筛选参数必须为正整数",
            )
        active_requested = annotation_contract.active_labels_by_ids(
            db, sorted(requested_label_ids)
        )
        if len(active_requested) != len(requested_label_ids):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="任务标签不存在或已停用，请刷新标签列表后重试",
            )

    plate_profile = str(training_recipe_key or "").strip()
    plate_profile_config = None
    plate_profile_preset_slug = None
    if plate_profile in PLATE_PROFILE_NAMES:
        plate_profile_preset_slug = profile_preset_slug(plate_profile)
        preset = crud_annotation_taxonomy.label_preset_get_by_slug(
            db, plate_profile_preset_slug
        )
        if (
            not preset
            or str(preset.training_profile or "").strip() != plate_profile
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="车牌训练预设不存在或未启用",
            )
        plate_profile_config = parse_profile_config(
            preset.training_profile_config,
            profile=plate_profile,
        )

    task_query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(Task.media_type == MediaType.IMAGE.value)
    if project_id is not None:
        task_query = task_query.filter(Task.project_id == int(project_id))
    scoped_dataset_ids = [int(value) for value in (dataset_ids or []) if int(value) > 0]
    if dataset_id is not None and int(dataset_id) not in scoped_dataset_ids:
        scoped_dataset_ids.insert(0, int(dataset_id))
    if scoped_dataset_ids:
        task_query = task_query.filter(Task.dataset_id.in_(scoped_dataset_ids))
    if dataset_category_ids:
        task_query = task_query.filter(
            Task.dataset_category_id.in_([int(value) for value in dataset_category_ids])
        )
    task_query = _filter_training_candidate_search(task_query, search)
    tasks = task_query.order_by(Task.id.desc()).all()
    rows: list[TaskTrainingCandidateResponse] = []
    for task in tasks:
        contract = annotation_contract.current_contract(db, int(task.id))
        if contract is None or str(contract.status or "") not in (
            annotation_contract.CURRENT_CONTRACT_TRAINING_STATUSES
        ):
            continue
        try:
            contract_label_ids = [
                int(value) for value in json.loads(contract.label_ids_json or "[]")
            ]
            labels = annotation_contract.active_labels_by_ids(db, contract_label_ids)
        except (TypeError, ValueError, json.JSONDecodeError, LabelUException):
            continue
        if not labels:
            continue
        # A task is eligible only when its current annotation contract covers
        # every selected label.  The contract, rather than historical sample
        # annotations, is the source of truth for the task-level filter.
        if requested_label_ids and not requested_label_ids.issubset(
            set(contract_label_ids)
        ):
            continue
        plate_audit = None
        done_sample_count = 0
        if plate_profile_config is not None:
            if not plate_recognition_train.task_matches_plate_profile(
                task, plate_profile_config
            ):
                continue
            plate_audit, _samples = plate_recognition_train.audit_task_done_samples(
                db,
                task_id=int(task.id),
                profile_config=plate_profile_config,
            )
            done_sample_count = int(plate_audit.done_sample_count)
            if str(task.source_type or "internal") == "third_party":
                done_samples = (
                    db.query(TaskSample)
                    .filter(
                        TaskSample.task_id == task.id,
                        TaskSample.state == SampleState.DONE.value,
                        TaskSample.deleted_at.is_(None),
                    )
                    .order_by(TaskSample.id.asc())
                    .all()
                )
                done_sample_count = len(done_samples)
                trainable_done_samples = [
                    sample
                    for sample in filter_trainable_samples(
                        db, task=task, samples=done_samples
                    )
                    if getattr(sample, "file", None) is not None
                ]
                plate_audit, _samples = plate_recognition_train.audit_plate_samples(
                    trainable_done_samples,
                    task_id=int(task.id),
                    profile_config=plate_profile_config,
                )
            complete_count = int(plate_audit.included_image_count)
        else:
            done_samples = (
                db.query(TaskSample)
                .filter(
                    TaskSample.task_id == task.id,
                    TaskSample.state == SampleState.DONE.value,
                    TaskSample.deleted_at.is_(None),
                )
                .order_by(TaskSample.id.asc())
                .all()
            )
            done_sample_count = len(done_samples)
            annotation_audit = audit_task_config_and_samples(
                task.config, crud_sample.list_by_task(db=db, task_id=int(task.id))
            )
            trainable_samples = [
                sample
                for sample in filter_trainable_samples(
                    db, task=task, samples=done_samples
                )
                if getattr(sample, "file", None) is not None
            ]
            task_config = parse_task_config(task.config)
            # Keep the candidate preview on the same admission contract as
            # automatic dataset freezing.  Without this audit, the preview
            # could report a DONE sample as trainable while the immutable
            # snapshot correctly excludes its incomplete tags or geometry.
            if task_config.get("tools") or task_config.get("tagList"):
                trainable_samples = [
                    sample
                    for sample in trainable_samples
                    if bool(audit_one_sample(sample, task_config).get("ok"))
                ]
            class_names = [str(label.label_value or "").strip() for label in labels]
            complete_count, _positive_count, _instance_count = count_detection_samples(
                trainable_samples,
                task_config,
                class_names or detection_class_names(task_config),
                parse_annotation=_parse_sample_annotation,
                is_negative=is_explicit_negative_sample,
            )
        sample_stats = _training_sample_stats(
            db,
            task=task,
            trainable_sample_count=int(complete_count),
            done_sample_count=int(done_sample_count),
            annotation_ready=(
                bool(annotation_audit.get("training_ready"))
                if plate_audit is None
                else None
            ),
        )
        rows.append(
            TaskTrainingCandidateResponse(
                task_id=task.id,
                task_name=task.name,
                **sample_stats,
                created_at=task.created_at,
                resolved_rect_preset_slug=(
                    plate_profile_preset_slug if plate_audit else ""
                ),
                match_source="plate_profile" if plate_audit else "task_contract",
                training_profile=(plate_profile if plate_audit else None),
                positive_image_count=(
                    int(plate_audit.positive_image_count) if plate_audit else 0
                ),
                negative_image_count=(
                    int(plate_audit.negative_image_count) if plate_audit else 0
                ),
                instance_count=int(plate_audit.instance_count) if plate_audit else 0,
                invalid_image_count=(
                    int(plate_audit.invalid_image_count) if plate_audit else 0
                ),
                contract_id=int(contract.id),
                contract_revision=int(contract.revision),
                label_ids=contract_label_ids,
                contract_labels=annotation_contract.labels_snapshot(labels),
                label_set_signature=contract.label_set_signature,
                class_order_signature=contract.class_order_signature,
                contract_status=str(contract.status or ""),
                dataset_category_id=task.dataset_category_id,
                dataset_category_name=(
                    task.dataset_category.name if task.dataset_category else None
                ),
                dataset_id=getattr(task, "dataset_id", None),
                dataset_label_contract_id=getattr(
                    task, "dataset_label_contract_id", None
                ),
                task_label_scope_mode=str(
                    getattr(task, "task_label_scope_mode", "FULL") or "FULL"
                ),
            )
        )
    total = len(rows)
    return rows[page * size : page * size + size], total


def _parse_task_config(raw: str | None) -> dict:
    if not raw or not str(raw).strip():
        return {}
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _task_rect_preset_slug(task: Task) -> str:
    cfg = _parse_task_config(task.config)
    return str(cfg.get("defaultRectPresetId") or "").strip()


def _rect_classes_from_label_preset(preset) -> list[str]:
    items = sorted(
        list(preset.items or []),
        key=lambda x: (int(getattr(x, "sort_order", 0) or 0), int(x.id)),
    )
    aliases = build_rect_label_slug_aliases(preset_label_items=items)
    out: list[str] = []
    seen: dict[str, None] = {}
    for item in items:
        val = str(getattr(item, "label_value", None) or "").strip()
        key = str(getattr(item, "label_key", None) or "").strip()
        slug = val if val else resolve_rect_label_slug(key, aliases)
        if slug and slug not in seen:
            seen[slug] = None
            out.append(slug)
    return out


def _task_rect_classes(task: Task) -> list[str]:
    seen: dict[str, None] = {}
    for tool in _parse_task_config(task.config).get("tools", []) or []:
        if not isinstance(tool, dict) or tool.get("tool") not in {"rectTool", "rect"}:
            continue
        config = tool.get("config") if isinstance(tool.get("config"), dict) else {}
        attrs = (
            config.get("attributes")
            if isinstance(config.get("attributes"), list)
            else []
        )
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            val = str(attr.get("value") or attr.get("key") or "").strip()
            if val and val not in seen:
                seen[val] = None
    return list(seen.keys())


def _resolve_task_rect_preset_slug(
    task: Task,
    *,
    preset_classes_by_slug: dict[str, list[str]],
) -> str:
    applied_slug = _task_rect_preset_slug(task)
    if applied_slug in preset_classes_by_slug:
        return applied_slug
    task_classes = _task_rect_classes(task)
    if not task_classes:
        return ""
    for slug, preset_classes in preset_classes_by_slug.items():
        if preset_classes == task_classes:
            return slug
    return ""


def _task_matches_rect_preset_filter(
    task: Task,
    rect_preset_slug: str,
    preset_classes_by_slug: dict[str, list[str]],
) -> bool:
    cfg = _parse_task_config(task.config)
    pool = (
        cfg.get("consistent_pool")
        if isinstance(cfg.get("consistent_pool"), dict)
        else {}
    )
    if pool.get("kind") == "train_filtered_consistent":
        stored = str(pool.get("rect_preset_slug") or "").strip()
        if stored and rect_preset_slug == stored:
            return True
    resolved_slug = _resolve_task_rect_preset_slug(
        task,
        preset_classes_by_slug=preset_classes_by_slug,
    )
    if rect_preset_slug == "__uncategorized__":
        return not resolved_slug
    return bool(resolved_slug) and resolved_slug == rect_preset_slug


def _task_training_candidate_match_source(
    task: Task,
    rect_preset_slug: str,
    preset_classes_by_slug: dict[str, list[str]],
) -> str | None:
    if _is_train_filtered_consistent_pool(task):
        return None
    applied_slug = _task_rect_preset_slug(task)
    if applied_slug and applied_slug == rect_preset_slug:
        return "explicit_preset"
    resolved_slug = _resolve_task_rect_preset_slug(
        task,
        preset_classes_by_slug=preset_classes_by_slug,
    )
    if resolved_slug != rect_preset_slug:
        return None
    return "class_fallback"


async def list_training_candidates(
    db: Session,
    current_user: User,
    *,
    rect_preset_slug: str,
    page: int,
    size: int,
    search: str | None = None,
) -> Tuple[List[TaskTrainingCandidateResponse], int]:
    slug = (rect_preset_slug or "").strip()
    if not slug:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="rect_preset_slug 不能为空",
        )

    presets, preset_classes_by_slug = _load_preset_classes_by_slug(db)
    if slug not in preset_classes_by_slug:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"rect_preset_slug={slug} 不存在，无法列出训练候选任务",
        )

    preset = next((row for row in presets if row.slug == slug), None)
    if preset and preset.training_profile in PLATE_PROFILE_NAMES:
        profile_config = parse_profile_config(
            preset.training_profile_config,
            profile=preset.training_profile,
        )
        query = crud_task.visible_query(
            db=db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        ).filter(Task.media_type == MediaType.IMAGE.value)
        query = _filter_training_candidate_search(query, search)
        candidates: list[tuple[Task, object]] = []
        for task in query.order_by(Task.id.desc()).all():
            if not plate_recognition_train.task_matches_plate_profile(
                task,
                profile_config,
            ):
                continue
            task_audit, _samples = plate_recognition_train.audit_task_done_samples(
                db,
                task_id=task.id,
                profile_config=profile_config,
            )
            if str(task.source_type or "internal") == "third_party":
                done_samples = (
                    db.query(TaskSample)
                    .filter(
                        TaskSample.task_id == task.id,
                        TaskSample.state == SampleState.DONE.value,
                        TaskSample.deleted_at.is_(None),
                    )
                    .order_by(TaskSample.id.asc())
                    .all()
                )
                task_audit, _samples = plate_recognition_train.audit_plate_samples(
                    filter_trainable_samples(db, task=task, samples=done_samples),
                    task_id=int(task.id),
                    profile_config=(
                        profile_config
                    ),
                )
            if task_audit.included_image_count:
                candidates.append((task, task_audit))
        total_task = len(candidates)
        paged = candidates[page * size : page * size + size]
        return (
            [
                TaskTrainingCandidateResponse(
                    task_id=task.id,
                    task_name=task.name,
                    training_ready=True,
                    done_count=task_audit.done_sample_count,
                    created_at=task.created_at,
                    resolved_rect_preset_slug=slug,
                    match_source="training_profile",
                    training_profile=str(preset.training_profile),
                    positive_image_count=task_audit.positive_image_count,
                    negative_image_count=task_audit.negative_image_count,
                    instance_count=task_audit.instance_count,
                    invalid_image_count=task_audit.invalid_image_count,
                )
                for task, task_audit in paged
            ],
            total_task,
        )

    tasks = _list_rect_preset_selectable_tasks(
        db,
        current_user,
        rect_preset_slug=slug,
        preset_classes_by_slug=preset_classes_by_slug,
        search=search,
    )
    candidate_statics = crud_sample.statics(
        db=db,
        task_ids=[task.id for task in tasks],
    )

    matched: list[tuple[Task, str]] = []
    for task in tasks:
        if task.training_ready is None:
            ensure_task_training_ready_refreshed(db, task)
        if _is_train_filtered_consistent_pool(task):
            if candidate_statics.get(f"{task.id}_{SampleState.DONE.value}", 0) <= 0:
                continue
            match_source = "consistent_pool"
        else:
            if task.training_ready is not True:
                continue
            match_source = _task_training_candidate_match_source(
                task,
                slug,
                preset_classes_by_slug,
            )
            if not match_source:
                continue
            if str(task.source_type or "internal") == "third_party":
                done_samples = (
                    db.query(TaskSample)
                    .filter(
                        TaskSample.task_id == task.id,
                        TaskSample.state == SampleState.DONE.value,
                        TaskSample.deleted_at.is_(None),
                    )
                    .all()
                )
                if not filter_trainable_samples(db, task=task, samples=done_samples):
                    continue
        matched.append((task, match_source))

    total_task = len(matched)
    paged = matched[page * size : page * size + size]
    statics = candidate_statics

    rows = [
        TaskTrainingCandidateResponse(
            task_id=task.id,
            task_name=task.name,
            training_ready=bool(task.training_ready),
            done_count=statics.get(f"{task.id}_{SampleState.DONE.value}", 0),
            created_at=task.created_at,
            resolved_rect_preset_slug=slug,
            match_source=match_source,
        )
        for task, match_source in paged
    ]
    return rows, total_task


def list_image_task_ids_matching_rect_preset(
    db: Session,
    current_user: User,
    rect_preset_slug: str,
) -> list[int]:
    """当前用户可见的、匹配拉框预设的图像任务 id（供 YOLO 训练作业列表筛选）。"""
    slug = (rect_preset_slug or "").strip()
    if not slug:
        return []
    _, preset_classes_by_slug = _load_preset_classes_by_slug(db)
    query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    )
    query = query.filter(Task.media_type == MediaType.IMAGE.value)
    task_ids = []
    for task in query.all():
        if not _task_matches_rect_preset_filter(task, slug, preset_classes_by_slug):
            continue
        if str(task.source_type or "internal") == "third_party":
            done_samples = (
                db.query(TaskSample)
                .filter(
                    TaskSample.task_id == task.id,
                    TaskSample.state == SampleState.DONE.value,
                    TaskSample.deleted_at.is_(None),
                )
                .all()
            )
            if not filter_trainable_samples(db, task=task, samples=done_samples):
                continue
        task_ids.append(task.id)
    return task_ids


def list_visible_image_task_ids_for_search(
    db: Session,
    current_user: User,
    rect_preset_slug: str | None = None,
    label_ids: list[int] | None = None,
    dataset_category_id: int | None = None,
    uncategorized: bool = False,
) -> list[int]:
    """图片搜索范围与任务列表当前的标签或旧预设筛选一致。"""
    normalized_label_ids = list(
        dict.fromkeys(int(value) for value in (label_ids or []))
    )
    if normalized_label_ids:
        label_query = (
            crud_task.visible_query(
                db=db,
                owner_id=current_user.id,
                list_all_tasks=is_task_superuser(current_user),
            )
            .with_entities(Task.id)
            .join(
                TaskAnnotationLabel,
                and_(
                    TaskAnnotationLabel.task_id == Task.id,
                    TaskAnnotationLabel.contract_id
                    == Task.current_annotation_contract_id,
                    TaskAnnotationLabel.status == "active",
                ),
            )
            .filter(
                Task.media_type == MediaType.IMAGE.value,
                TaskAnnotationLabel.label_id.in_(normalized_label_ids),
            )
        )
        if dataset_category_id is not None:
            label_query = label_query.filter(
                Task.dataset_category_id == int(dataset_category_id)
            )
        elif uncategorized:
            label_query = label_query.filter(Task.dataset_category_id.is_(None))
        return [
            int(row[0])
            for row in label_query.group_by(Task.id)
            .having(
                func.count(func.distinct(TaskAnnotationLabel.label_id))
                == len(normalized_label_ids)
            )
            .all()
        ]
    slug = (rect_preset_slug or "").strip()
    query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    )
    query = query.filter(Task.media_type == MediaType.IMAGE.value)
    if dataset_category_id is not None:
        query = query.filter(Task.dataset_category_id == int(dataset_category_id))
    elif uncategorized:
        query = query.filter(Task.dataset_category_id.is_(None))
    tasks = query.all()
    if not slug:
        return [task.id for task in tasks]
    _, preset_classes_by_slug = _load_preset_classes_by_slug(db)
    return [
        task.id
        for task in tasks
        if _task_matches_rect_preset_filter(
            task,
            slug,
            preset_classes_by_slug,
        )
    ]


async def search_images(
    db: Session,
    current_user: User,
    *,
    keyword: str,
    page: int,
    size: int,
    rect_preset_slug: str | None = None,
    label_ids: list[int] | None = None,
    dataset_category_id: int | None = None,
    uncategorized: bool = False,
) -> Tuple[List[TaskImageSearchHit], int]:
    q = (keyword or "").strip()
    if not q:
        return [], 0
    if size < 1:
        size = 20
    if size > 100:
        size = 100
    task_ids = list_visible_image_task_ids_for_search(
        db,
        current_user,
        rect_preset_slug=rect_preset_slug,
        label_ids=label_ids,
        dataset_category_id=dataset_category_id,
        uncategorized=uncategorized,
    )
    rows, total = crud_sample.search_by_filename(
        db, task_ids=task_ids, keyword=q, page=page, size=size
    )
    hits: List[TaskImageSearchHit] = []
    for sample in rows:
        att = sample.file
        if not att or not sample.task:
            continue
        file_resp = build_attachment_response(att)
        hits.append(
            TaskImageSearchHit(
                sample_id=sample.id,
                task_id=sample.task_id,
                task_name=sample.task.name or "",
                filename=att.filename or "",
                state=sample.state,
                url=getattr(file_resp, "url", None),
                thumbnail_url=getattr(file_resp, "thumbnail_url", None),
            )
        )
    return hits, total


def _load_preset_classes_by_slug(db: Session) -> tuple[list, dict[str, list[str]]]:
    presets, _ = crud_annotation_taxonomy.label_preset_list(db, page=0, size=500)
    return presets, {p.slug: _rect_classes_from_label_preset(p) for p in presets}


async def rect_preset_summary(
    db: Session,
    current_user: User,
) -> TaskRectPresetSummaryResponse:
    presets, preset_classes_by_slug = _load_preset_classes_by_slug(db)
    visible_tasks = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).all()
    counts_by_slug: dict[str, int] = {}
    uncategorized = 0
    for task in visible_tasks:
        slug = _resolve_task_rect_preset_slug(
            task,
            preset_classes_by_slug=preset_classes_by_slug,
        )
        if not slug:
            uncategorized += 1
            continue
        counts_by_slug[slug] = counts_by_slug.get(slug, 0) + 1

    return TaskRectPresetSummaryResponse(
        all=len(visible_tasks),
        presets=[
            TaskRectPresetSummaryItem(
                slug=p.slug,
                name=p.name,
                count=counts_by_slug.get(p.slug, 0),
            )
            for p in presets
        ],
        uncategorized=uncategorized,
    )


async def get(db: Session, task_id: int, current_user: User) -> TaskResponseWithStatics:

    # get task detail
    task = crud_task.get(db=db, task_id=task_id)

    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_has_task_access(current_user, task):
        logger.error(
            "cannot get task, the task owner is:{}, the get operator is:{}",
            task.created_by,
            current_user.id,
        )
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # get progress
    statics = crud_sample.statics(
        db=db,
        task_ids=[task.id],
    )

    # response
    return _task_response_with_statics(db, task, statics)


async def get_with_compare_sample_access(
    db: Session,
    *,
    task_id: int,
    sample_id: int,
    compare_access_token: str,
    current_user: User,
) -> TaskResponseWithStatics:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if user_has_task_access(current_user, task):
        statics = crud_sample.statics(db=db, task_ids=[task.id])
        return _task_response_with_statics(db, task, statics)

    sample = crud_sample.get(db=db, sample_id=sample_id)
    if not sample or int(sample.task_id) != int(task_id):
        logger.error(
            "compare access sample mismatch task_id={} sample_id={}", task_id, sample_id
        )
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not verify_compare_sample_access_token(
        compare_access_token,
        task_id=task_id,
        sample_id=sample_id,
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    statics = crud_sample.statics(db=db, task_ids=[task.id])
    return _task_response_with_statics(db, task, statics)


async def get_collaborators(db: Session, task_id: int, current_user: User):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_has_task_access(current_user, task):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return [
        UserResponse(id=user.id, username=user.username) for user in task.collaborators
    ]


async def add_collaborator(db: Session, task_id: int, user_id: int, current_user: User):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_is_task_owner_or_superuser(current_user, task):
        logger.error(
            "cannot add collaborator, the task owner is:{}, the add operator is:{}",
            task.created_by,
            current_user.id,
        )
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    user = crud_user.get(db=db, id=user_id)
    if not user:
        logger.error("cannot find user:{}", user_id)
        raise LabelUException(
            code=ErrorCode.CODE_40002_USER_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # collaborator exists
    if user in task.collaborators:
        logger.error("collaborator already exists:{}", user_id)
        raise LabelUException(
            code=ErrorCode.CODE_50003_COLLABORATOR_ALREADY_EXISTS,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    with begin_transaction(db):
        task.collaborators.append(user)

    return UserResponse(id=user.id, username=user.username)


async def batch_add_collaborators(
    db: Session, task_id: int, user_ids: List[int], current_user: User
):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_is_task_owner_or_superuser(current_user, task):
        logger.error(
            "cannot add collaborator, the task owner is:{}, the add operator is:{}",
            task.created_by,
            current_user.id,
        )
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    user_ids = (
        set(user_ids)
        - set([user.id for user in task.collaborators])
        - set([task.created_by])
    )
    users = crud_user.list_by_ids(db=db, ids=user_ids)
    if len(users) != len(user_ids):
        logger.error("cannot find users:{}", user_ids)
        raise LabelUException(
            code=ErrorCode.CODE_40002_USER_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    with begin_transaction(db):
        task.collaborators.extend(users)

    return [UserResponse(id=user.id, username=user.username) for user in users]


async def remove_collaborator(
    db: Session, task_id: int, user_id: int, current_user: User
):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_is_task_owner_or_superuser(current_user, task):
        logger.error(
            "cannot remove collaborator, the task owner is:{}, the remove operator is:{}",
            task.created_by,
            current_user.id,
        )
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    user = crud_user.get(db=db, id=user_id)
    if not user:
        logger.error("cannot find user:{}", user_id)
        raise LabelUException(
            code=ErrorCode.CODE_40002_USER_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if user not in task.collaborators:
        logger.error("cannot find collaborator:{}", user_id)
        raise LabelUException(
            code=ErrorCode.CODE_50004_COLLABORATOR_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    with begin_transaction(db):
        task.collaborators.remove(user)

    return CommonDataResp(ok=True)


async def batch_remove_collaborators(
    db: Session, task_id: int, user_ids: List[int], current_user: User
):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_is_task_owner_or_superuser(current_user, task):
        logger.error(
            "cannot remove collaborator, the task owner is:{}, the remove operator is:{}",
            task.created_by,
            current_user.id,
        )
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    users = crud_user.list_by_ids(db=db, ids=user_ids)
    if len(users) != len(user_ids):
        logger.error("cannot find users:{}", user_ids)
        raise LabelUException(
            code=ErrorCode.CODE_40002_USER_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    with begin_transaction(db):
        for user in users:
            if user in task.collaborators:
                task.collaborators.remove(user)

    return CommonDataResp(ok=True)


def _can_manage_project(db: Session, project: Project, user: User) -> bool:
    if is_task_superuser(user) or project.owner_id == user.id:
        return True
    member = crud_project.get_member(db, project.id, user.id)
    return bool(
        member and member.status == "ACTIVE" and member.role in {"owner", "admin"}
    )


async def batch_assign_project(
    db: Session,
    cmd: BatchAssignTaskProjectCommand,
    current_user: User,
) -> TaskProjectAssignmentResponse:
    """Attach legacy or unscoped tasks to a managed 7GAI project.

    This is intentionally explicit instead of treating NULL project IDs as
    belonging to every project. The assignment is audited and idempotent.
    """
    from labelu.internal.common.phase_one_contract import (
        ensure_legacy_control_plane_retired,
    )

    ensure_legacy_control_plane_retired()
    project = crud_project.get(db, cmd.project_id)
    if project is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="目标项目不存在",
        )
    if not _can_manage_project(db, project, current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="无权管理目标项目",
        )

    task_ids = list(dict.fromkeys(int(value) for value in cmd.task_ids))
    tasks = (
        db.query(Task)
        .filter(Task.id.in_(task_ids), Task.deleted_at.is_(None))
        .order_by(Task.id.asc())
        .all()
    )
    if len(tasks) != len(task_ids):
        missing = sorted(set(task_ids) - {int(task.id) for task in tasks})
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"任务不存在或已删除: {missing}",
        )
    unauthorized = [
        int(task.id)
        for task in tasks
        if not user_is_task_owner_or_superuser(current_user, task)
    ]
    if unauthorized:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message=f"无权归属这些任务: {unauthorized}",
        )

    changed_ids: list[int] = []
    previous_projects: dict[int, int | None] = {}
    with begin_transaction(db):
        for task in tasks:
            previous_project = (
                int(task.project_id) if task.project_id is not None else None
            )
            previous_projects[int(task.id)] = previous_project
            if previous_project != int(project.id):
                task.project_id = int(project.id)
                task.updated_by = current_user.id
                changed_ids.append(int(task.id))
        db.flush()

    biz_log(
        "task.project_assignment",
        user_id=int(current_user.id),
        task_ids=task_ids,
        project_id=int(project.id),
        changed_task_ids=changed_ids,
        previous_projects=previous_projects,
    )
    return TaskProjectAssignmentResponse(
        task_ids=task_ids,
        project_id=int(project.id),
        updated_count=len(changed_ids),
    )


async def assign_project(
    db: Session,
    task_id: int,
    cmd: AssignTaskProjectCommand,
    current_user: User,
) -> TaskProjectAssignmentResponse:
    return await batch_assign_project(
        db,
        BatchAssignTaskProjectCommand(task_ids=[task_id], project_id=cmd.project_id),
        current_user,
    )


async def update(
    db: Session, task_id: int, cmd: UpdateCommand, current_user: User
) -> TaskResponse:

    # get task
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_has_task_access(current_user, task):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # update
    obj_in = cmd.dict(exclude_unset=True)
    if "dataset_category_id" in obj_in:
        category = _active_dataset_category(db, cmd.dataset_category_id)
        obj_in[Task.dataset_category_id.key] = int(category.id) if category else None
    if "dataset_id" in obj_in or "dataset_label_contract_id" in obj_in:
        effective_dataset_id = obj_in.get("dataset_id", task.dataset_id)
        effective_contract_id = obj_in.get(
            "dataset_label_contract_id", task.dataset_label_contract_id
        )
        if "dataset_id" in obj_in and effective_dataset_id is None:
            effective_contract_id = None
        bind_cmd = BasicConfigCommand(
            name=task.name or "task",
            media_type=task.media_type,
            dataset_category_id=obj_in.get(
                "dataset_category_id", task.dataset_category_id
            ),
            dataset_id=effective_dataset_id,
            dataset_label_contract_id=effective_contract_id,
            task_label_scope_mode=obj_in.get(
                "task_label_scope_mode", task.task_label_scope_mode
            ),
        )
        dataset, dataset_contract, scope_mode = _resolve_task_dataset_binding(
            db, cmd=bind_cmd, current_user=current_user
        )
        obj_in[Task.dataset_id.key] = int(dataset.id) if dataset else None
        obj_in[Task.dataset_label_contract_id.key] = (
            int(dataset_contract.id) if dataset_contract else None
        )
        obj_in[Task.task_label_scope_mode.key] = scope_mode
    if Task.config.key in obj_in:
        if cmd.config is not None and str(cmd.config).strip():
            try:
                incoming_config = json.loads(cmd.config)
            except json.JSONDecodeError as e:
                logger.error("invalid task config JSON: {}", e)
                raise LabelUException(
                    code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            existing_config = _parse_task_config(task.config)
            existing_profile = profile_for_task_config(existing_config)
            existing_slug = str(
                existing_config.get("defaultRectPresetId") or ""
            ).strip()
            if existing_slug == PLATE_RECOGNITION_PRESET_SLUG:
                existing_profile = PLATE_RECOGNITION_PROFILE
            elif existing_slug == PLATE_DETECTION_PRESET_SLUG:
                existing_profile = PLATE_DETECTION_PROFILE
            incoming_profile = profile_for_task_config(incoming_config)
            incoming_is_plate = task_config_has_plate_recognition_marker(
                incoming_config
            ) and incoming_profile in PLATE_PROFILE_NAMES
            if incoming_is_plate:
                if (
                    incoming_profile == PLATE_RECOGNITION_PROFILE
                    and existing_profile != PLATE_RECOGNITION_PROFILE
                ):
                    raise LabelUException(
                        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                        status_code=status.HTTP_409_CONFLICT,
                        message=(
                            "一期车牌训练只支持矩形框。请使用普通目标检测预设，"
                            "创建“车牌”或“数字位置”标签；历史复合车牌任务仅允许查看和续用。"
                        ),
                    )
                profile_config = (
                    DEFAULT_PLATE_DETECTION_CONFIG
                    if incoming_profile == PLATE_DETECTION_PROFILE
                    else DEFAULT_PLATE_RECOGNITION_CONFIG
                )
                incoming_config = normalize_plate_recognition_task_config(
                    incoming_config,
                    profile_config,
                )
            effective_media_type = cmd.media_type or task.media_type
            if effective_media_type:
                obj_in[Task.config.key] = json.dumps(
                    incoming_config, ensure_ascii=False
                )
                obj_in[Task.status.key] = TaskStatus.CONFIGURED
            else:
                obj_in[Task.config.key] = None
        else:
            obj_in[Task.config.key] = None
    with begin_transaction(db):
        updated_task = crud_task.update(db=db, db_obj=task, obj_in=obj_in)

    if Task.config.key in obj_in and updated_task.media_type == MediaType.IMAGE.value:
        from labelu.internal.application.service.auto_sample_preannotate import (
            schedule_auto_preannotate_for_task_samples,
        )

        schedule_auto_preannotate_for_task_samples(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            source_hint="task_config_update",
        )

    # response
    return TaskResponse(
        id=updated_task.id,
        project_id=updated_task.project_id,
        name=updated_task.name,
        description=updated_task.description,
        tips=updated_task.tips,
        media_type=updated_task.media_type,
        config=updated_task.config,
        status=updated_task.status,
        created_at=updated_task.created_at,
        created_by=UserResp(
            id=updated_task.owner.id,
            username=updated_task.owner.username,
        ),
        source_type=str(updated_task.source_type or "internal"),
        source_system=updated_task.source_system,
        source_external_task_id=updated_task.source_external_task_id,
        source_external_package_id=updated_task.source_external_package_id,
        source_integration_id=updated_task.source_integration_id,
        dataset_id=getattr(updated_task, "dataset_id", None),
        dataset_label_contract_id=getattr(
            updated_task, "dataset_label_contract_id", None
        ),
        task_label_scope_mode=str(
            getattr(updated_task, "task_label_scope_mode", "FULL") or "FULL"
        ),
        **_dataset_category_fields(updated_task),
    )


async def delete(db: Session, task_id: int, current_user: User) -> CommonDataResp:

    # get task
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user_is_task_owner_or_superuser(current_user, task):
        logger.error(
            "cannot delete task, the task owner is:{}, the delete operator is:{}",
            task.created_by,
            current_user.id,
        )
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Hard-delete task-owned rows in FK order.  Shared training outputs are
    # reassigned by the cleanup service and are removed only when task-owned.
    with begin_transaction(db):
        cleanup = cleanup_task_records(db=db, task_id=task_id)

    remove_task_artifacts(db=db, task_id=task_id, result=cleanup)
    return CommonDataResp(ok=True)
