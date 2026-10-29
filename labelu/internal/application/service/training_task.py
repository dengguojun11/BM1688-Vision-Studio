from __future__ import annotations

import json
import re
import hashlib
import asyncio
from pathlib import Path
from collections import OrderedDict
from typing import Any, Optional

from fastapi import status
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_algorithm,
    crud_annotation_taxonomy,
    crud_sample,
    crud_task,
    crud_training_task,
    crud_yolo_train_job,
)
from labelu.internal.application.command.training_task import (
    CreateLabelDimensionTrainingTaskCommand,
    PreviewLabelDimensionTrainingTaskCommand,
    SelectedLabelItemInput,
    SelectedPresetInput,
)
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.response.training_task import (
    LabelDimensionTrainingPreviewResponse,
    MergedTrainingClassResponse,
    SelectedLabelItemSnapshotResponse,
    SelectedPresetSnapshotResponse,
    TrainingTaskCreateResponse,
    TrainingTaskDetailResponse,
    TrainingTaskIgnoredTaskResponse,
    TrainingTaskIncludedTaskResponse,
    TrainingTaskListRowResponse,
)
from labelu.internal.application.service.yolo_dataset import (
    build_yolo_rect_class_names,
    _parse_sample_annotation,
)
from labelu.internal.application.service import plate_recognition_train
from labelu.internal.application.service import (
    yolo_quality_policy as quality_policy_service,
)
from labelu.internal.common.plate_recognition_profile import (
    PLATE_RECOGNITION_PRESET_SLUG,
    PLATE_RECOGNITION_PROFILE,
    parse_profile_config,
)
from labelu.internal.common.yolo_rect_class_slug import looks_like_yolo_class_slug
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.idempotency import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    normalize_idempotency_key,
    request_payload_hash,
)
from labelu.internal.common.project_scope import ProjectScopeError, assert_project_chain, task_project_id
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.common.text_integrity import contains_encoding_replacement_marker
from labelu.internal.common.training_sample import is_explicit_negative_sample
from labelu.internal.common.yolo_detection_geometry import (
    detection_rects_for_sample,
    parse_task_config,
)
from labelu.internal.common.label_contract import (
    annotation_sha256,
    class_order_signature,
    label_set_signature,
)
from labelu.internal.common.config import settings
from labelu.internal.common.phase_one_contract import ensure_phase_one_training_job_type
from labelu.internal.common.training_contract import (
    branch_identity_sha256,
    build_selection_contract,
    canonical_runtime_profile,
    canonical_training_recipe_key,
    canonical_selection_contract,
    selection_contract_json,
    selection_contract_sha256,
)
from labelu.internal.common.training_sample_conditions import (
    sample_condition_values,
    sample_matches_conditions,
    validate_sample_conditions,
)
from labelu.internal.application.service import (
    annotation_contract as annotation_contract_service,
)
from labelu.internal.application.service import dataset as dataset_service
from labelu.internal.application.service import model_series as model_series_service
from labelu.internal.application.service.training_review_gate import (
    filter_trainable_samples,
)
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationScope,
    TaskAnnotationContract,
    TaskAnnotationLabel,
)
from labelu.internal.domain.models.annotation_taxonomy import (
    DatasetCategory,
    RuntimePublishTemplate,
    TrainingRecipe,
)
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User

TRAINING_TASK_MODE_LABEL_DIMENSION = "label_dimension"
TRAINING_TASK_MODE_TASK_DIMENSION = "task_dimension"
_DUPLICATE_NAME_MESSAGE = "该任务名称已存在，请重新修改后创建"
_CORRUPTED_NAME_MESSAGE = "训练任务名称疑似乱码，请使用 UTF-8 编码重新提交"


def _effective_dataset_ids(cmd: Any) -> list[int]:
    """Return the deduplicated dataset scope with legacy ``dataset_id`` first."""

    values: list[int] = []
    primary = getattr(cmd, "dataset_id", None)
    if primary not in (None, "", 0):
        values.append(int(primary))
    for value in getattr(cmd, "dataset_ids", None) or []:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized > 0 and normalized not in values:
            values.append(normalized)
    return values


def _primary_dataset_id(cmd: Any, tasks: list[Task] | None = None) -> int | None:
    """Resolve the immutable snapshot owner while retaining old callers."""

    ids = _effective_dataset_ids(cmd)
    if ids:
        return ids[0]
    for task in tasks or []:
        value = getattr(task, "dataset_id", None)
        if value not in (None, "", 0):
            return int(value)
    return None


def _dataset_contract_identity(
    db: Session, dataset: Dataset
) -> tuple[str, str, tuple[int, ...]]:
    """Extract the immutable label contract identity for compatibility checks."""

    # Resolve the effective contract instead of trusting a possibly stale ORM
    # relationship.  Cross-dataset training must fail closed when an older
    # dataset has no complete immutable contract; silently treating it as
    # compatible would produce a snapshot whose class mapping cannot be
    # reconstructed later.
    contract = dataset_service._dataset_contract_or_none(db, dataset)
    label_signature = str(
        getattr(contract, "label_set_signature", None)
        or getattr(dataset, "dataset_label_set_signature", None)
        or ""
    ).strip()
    order_signature = str(
        getattr(contract, "class_order_signature", None)
        or getattr(dataset, "dataset_class_order_signature", None)
        or ""
    ).strip()
    label_ids: tuple[int, ...] = ()
    raw_ids = getattr(contract, "label_ids_json", None)
    try:
        parsed = json.loads(raw_ids or "[]")
        if isinstance(parsed, list):
            label_ids = tuple(int(value) for value in parsed if int(value) > 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        label_ids = ()
    return label_signature, order_signature, label_ids


def _validate_dataset_scope(
    db: Session,
    *,
    current_user: User,
    cmd: Any,
    tasks: list[Task] | None = None,
) -> tuple[int | None, list[int]]:
    """Validate a multi-dataset selection and return primary/scope IDs.

    Different logical datasets may be merged only when their frozen label
    contracts are provably identical.  The first selected dataset remains the
    snapshot owner for backward-compatible ``DatasetVersion.dataset_id`` rows;
    the full scope is persisted in the manifest and selection contract.
    """

    task_rows = tasks or []
    scope = _effective_dataset_ids(cmd)
    for task in task_rows:
        value = getattr(task, "dataset_id", None)
        if value not in (None, "", 0) and int(value) not in scope:
            scope.append(int(value))
    primary_id = _primary_dataset_id(cmd, task_rows)
    if primary_id is None and scope:
        primary_id = scope[0]
    if primary_id is None:
        return None, []
    if primary_id not in scope:
        scope.insert(0, primary_id)

    datasets: list[Dataset] = []
    for dataset_id in scope:
        dataset = dataset_service._dataset_or_raise(db, int(dataset_id), current_user)
        datasets.append(dataset)
    primary = datasets[0]
    primary_identity = _dataset_contract_identity(db, primary)
    primary_category = getattr(primary, "dataset_category_id", None)
    for dataset in datasets[1:]:
        identity = _dataset_contract_identity(db, dataset)
        # A cross-dataset merge is only reproducible when both sides expose a
        # complete contract identity.  Legacy rows without signatures are
        # intentionally rejected instead of being guessed compatible.
        if not all((primary_identity[0], primary_identity[1], primary_identity[2])) or not all(
            (identity[0], identity[1], identity[2])
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="所选数据集缺少完整标签契约，无法安全合并训练",
            )
        if primary_identity[0] != identity[0]:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="所选数据集的标签集合不一致，不能合并训练",
            )
        if primary_identity[1] != identity[1]:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="所选数据集的标签顺序不一致，不能合并训练",
            )
        if primary_identity[2] != identity[2]:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="所选数据集的标签契约不一致，不能合并训练",
            )
        if (
            primary_category is None
            or getattr(dataset, "dataset_category_id", None) is None
            or int(getattr(dataset, "dataset_category_id")) != int(primary_category)
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="所选数据集分类不一致，不能合并训练",
            )
    return int(primary_id), scope


def _normalize_training_contract_command(cmd):
    """Normalize legacy recipe/runtime spellings at the service boundary."""

    recipe_key = (
        canonical_training_recipe_key(cmd.training_recipe_key)
        if cmd.training_recipe_key is not None
        else None
    )
    runtime_profile = (
        canonical_runtime_profile(cmd.runtime_profile)
        if cmd.runtime_profile is not None
        else None
    )
    if (
        recipe_key == cmd.training_recipe_key
        and runtime_profile == cmd.runtime_profile
    ):
        return cmd
    return cmd.model_copy(
        update={
            "training_recipe_key": recipe_key,
            "runtime_profile": runtime_profile,
        }
    )


def _build_training_selection_contract(
    cmd: Any,
    *,
    mode: str,
    source_task_ids: list[int],
    selected_label_ids: list[int],
    class_names: list[str],
    dataset_version_id: int | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Build the immutable branch contract used by task-dimension training.

    Server-derived values overwrite client copies.  This keeps the hash tied
    to the snapshot the backend actually validated while retaining forward
    compatible fields sent by newer clients.
    """

    generated = build_selection_contract(
        dataset_id=cmd.dataset_id,
        dataset_ids=_effective_dataset_ids(cmd),
        dataset_version_id=dataset_version_id,
        mode=mode,
        task_ids=source_task_ids,
        selected_label_ids=selected_label_ids,
        sample_conditions=cmd.sample_conditions,
        annotation_contract={
            "class_order": list(class_names),
            "label_set_signature": (
                label_set_signature(selected_label_ids)
                if selected_label_ids
                else None
            ),
        },
        recipe={
            "training_recipe_key": cmd.training_recipe_key,
            "training_recipe_version": cmd.training_recipe_version,
            "runtime_profile": cmd.runtime_profile,
            "runtime_interface_version": cmd.runtime_interface_version,
        },
    )
    supplied = canonical_selection_contract(cmd.selection_contract)
    # Preserve explicitly supplied extension keys but keep all server-owned
    # selection dimensions authoritative.
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
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="branch_key 与当前训练选择合同不一致，请刷新预览后重试",
        )
    return contract, digest, selection_contract_json(contract)


def _decorate_preview_contract(
    preview: LabelDimensionTrainingPreviewResponse,
    *,
    cmd: Any,
    mode: str,
    task_ids: list[int],
) -> LabelDimensionTrainingPreviewResponse:
    """Attach the branch contract/hash to a read-only preview response."""

    class_names = [str(item.class_name) for item in preview.merged_classes]
    contract, digest, _serialized = _build_training_selection_contract(
        cmd,
        mode=mode,
        source_task_ids=task_ids,
        selected_label_ids=[int(item) for item in preview.selected_label_ids],
        class_names=class_names,
    )
    preview.branch_key = branch_identity_sha256(contract)
    preview.selection_contract_sha256 = digest
    preview.selection_contract = contract
    return preview


def _validated_sample_conditions(
    db: Session, conditions: dict[str, list[str]] | None
) -> dict[str, list[str]]:
    return validate_sample_conditions(db, conditions)


def _sample_matches_conditions(
    result: dict[str, Any], conditions: dict[str, list[str]]
) -> tuple[bool, dict[str, str]]:
    return sample_matches_conditions(result, conditions)


def _sample_condition_values(result: dict[str, Any]) -> dict[str, list[str]]:
    return sample_condition_values(result)


def _increment_sample_condition_counts(
    counts: dict[str, dict[str, int]], result: dict[str, Any]
) -> None:
    for key, values in _sample_condition_values(result).items():
        value_counts = counts.setdefault(key, {})
        for value in values:
            value_counts[value] = value_counts.get(value, 0) + 1


def _normalize_name(name: str) -> str:
    return str(name or "").strip()


def _validate_name(name: str) -> str:
    normalized = _normalize_name(name)
    if not normalized:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练任务名称不能为空",
        )
    if contains_encoding_replacement_marker(normalized):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=_CORRUPTED_NAME_MESSAGE,
        )
    return normalized


def _is_task_dimension_request(cmd: PreviewLabelDimensionTrainingTaskCommand) -> bool:
    return bool(cmd.selected_task_ids) and not (
        cmd.label_ids or cmd.selected_presets or cmd.selected_label_items
    )


def _validate_task_dimension_dataset_binding(
    db: Session,
    *,
    current_user: User,
    selected_task_ids: list[int] | None,
    dataset_id: int | None,
    dataset_ids: list[int] | None = None,
) -> None:
    """Reject a stale task selection before it can create a mixed snapshot.

    The candidate endpoint scopes rows by logical dataset.  Re-check that
    binding against the unscoped visible task query because a task can be
    moved or deleted between the candidate request and the create request.
    """

    allowed_dataset_ids = []
    if dataset_id is not None:
        allowed_dataset_ids.append(int(dataset_id))
    for value in dataset_ids or []:
        if int(value) not in allowed_dataset_ids:
            allowed_dataset_ids.append(int(value))
    if not allowed_dataset_ids or not selected_task_ids:
        return
    task_ids = [int(value) for value in selected_task_ids]
    visible_tasks = (
        crud_task.visible_query(
            db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        .filter(Task.id.in_(task_ids), Task.media_type == MediaType.IMAGE.value)
        .all()
    )
    by_id = {int(task.id): task for task in visible_tasks}
    missing = [task_id for task_id in task_ids if task_id not in by_id]
    if missing:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="所选训练任务已不可访问或不再是图片任务，请刷新候选任务后重试",
        )
    mismatched = [
        task_id
        for task_id in task_ids
        if int(getattr(by_id[task_id], "dataset_id", 0) or 0)
        not in set(allowed_dataset_ids)
    ]
    if mismatched:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="所选训练任务已不属于当前逻辑数据集范围，请刷新候选任务后重试",
        )


def _validate_selected_tasks_in_preview(
    *,
    selected_task_ids: list[int] | None,
    preview: LabelDimensionTrainingPreviewResponse,
) -> None:
    """Prevent the create path from silently dropping a selected task."""

    selected_ids = {int(value) for value in (selected_task_ids or [])}
    if not selected_ids:
        return
    included_ids = {int(item.task_id) for item in preview.included_tasks}
    missing = sorted(selected_ids - included_ids)
    if not missing:
        return
    ignored_by_id = {
        int(item.task_id): str(item.reason_text or "训练样本已不满足当前条件")
        for item in preview.ignored_tasks
    }
    details = "；".join(
        f"任务 #{task_id}：{ignored_by_id.get(task_id, '候选状态已变化')}"
        for task_id in missing
    )
    raise LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=status.HTTP_409_CONFLICT,
        message=f"所选任务的训练状态已变化，未能全部纳入训练快照（{details}），请刷新候选任务后重试",
    )


def _with_plate_profile_for_task_dimension(
    db: Session,
    cmd: PreviewLabelDimensionTrainingTaskCommand,
) -> PreviewLabelDimensionTrainingTaskCommand:
    if str(cmd.training_recipe_key or "").strip() != PLATE_RECOGNITION_PROFILE:
        return cmd
    preset = crud_annotation_taxonomy.label_preset_get_by_slug(
        db, PLATE_RECOGNITION_PRESET_SLUG
    )
    if (
        not preset
        or str(preset.training_profile or "").strip() != PLATE_RECOGNITION_PROFILE
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="车牌复合识别预设不存在或未启用",
        )
    profile = parse_profile_config(preset.training_profile_config)
    rect_label = str(profile.get("rect_label") or "").strip()
    if not any(
        str(item.label_value or "").strip() == rect_label for item in preset.items
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="车牌复合识别预设缺少检测标签",
        )
    return cmd.model_copy(
        update={
            "selected_presets": [
                SelectedPresetInput(preset_slug=PLATE_RECOGNITION_PRESET_SLUG)
            ],
            "selected_label_items": [
                SelectedLabelItemInput(
                    preset_slug=PLATE_RECOGNITION_PRESET_SLUG,
                    label_value=rect_label,
                )
            ],
        }
    )


def _resolve_plate_dataset_category(
    db: Session,
    *,
    cmd: PreviewLabelDimensionTrainingTaskCommand,
    tasks: list[Task],
) -> DatasetCategory | None:
    category_ids = list(cmd.dataset_category_ids or [])
    if (
        cmd.dataset_category_id is not None
        and int(cmd.dataset_category_id) not in category_ids
    ):
        category_ids.append(int(cmd.dataset_category_id))

    categories_by_id: dict[int, DatasetCategory] = {}
    if category_ids:
        rows = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.id.in_(category_ids),
                DatasetCategory.status == "active",
                DatasetCategory.deleted_at.is_(None),
            )
            .all()
        )
        categories_by_id = {int(row.id): row for row in rows}
        if set(categories_by_id) != set(category_ids):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="数据集分类不存在或已停用",
            )
        mismatched_task_ids = [
            int(task.id)
            for task in tasks
            if task.dataset_category_id not in categories_by_id
        ]
        if mismatched_task_ids:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"所选任务不属于当前数据集分类：{mismatched_task_ids}",
            )

    if cmd.dataset_category_id is not None:
        return categories_by_id[int(cmd.dataset_category_id)]

    source_category_ids = {
        int(task.dataset_category_id)
        for task in tasks
        if task.dataset_category_id is not None
    }
    if len(source_category_ids) != 1 or any(
        task.dataset_category_id is None for task in tasks
    ):
        return None
    category_id = next(iter(source_category_ids))
    if categories_by_id:
        return categories_by_id.get(category_id)
    rows = (
        db.query(DatasetCategory)
        .filter(
            DatasetCategory.id == category_id,
            DatasetCategory.status == "active",
            DatasetCategory.deleted_at.is_(None),
        )
        .first()
    )


def check_training_task_name_available(db: Session, owner_id: int, name: str) -> bool:
    normalized = _validate_name(name)
    return not crud_training_task.name_exists(db, owner_id, normalized)


def create_training_task_row(
    db: Session,
    *,
    owner_id: int,
    name: str,
    mode: str,
    status: str,
    primary_task_id: Optional[int],
    selected_presets_snapshot: Any,
    selected_label_items_snapshot: Any,
    merged_class_names_snapshot: Any,
    source_tasks_snapshot: Any,
    ignored_tasks_snapshot: Any,
    preview_summary: Any | None = None,
    latest_job_id: int | None = None,
    latest_job_status: str | None = None,
    series_id: int | None = None,
    variant_id: int | None = None,
    branch_key: str | None = None,
    selection_contract_sha256: str | None = None,
    selection_contract_json: str | None = None,
    sample_conditions_json: str | None = None,
):
    normalized_name = _validate_name(name)
    return crud_training_task.create(
        db,
        owner_id=owner_id,
        name=normalized_name,
        mode=mode,
        status=status,
        primary_task_id=primary_task_id,
        selected_presets_snapshot=selected_presets_snapshot,
        selected_label_items_snapshot=selected_label_items_snapshot,
        merged_class_names_snapshot=merged_class_names_snapshot,
        source_tasks_snapshot=source_tasks_snapshot,
        ignored_tasks_snapshot=ignored_tasks_snapshot,
        preview_summary=preview_summary,
        latest_job_id=latest_job_id,
        latest_job_status=latest_job_status,
        series_id=series_id,
        variant_id=variant_id,
        branch_key=branch_key,
        selection_contract_sha256=selection_contract_sha256,
        selection_contract_json=selection_contract_json,
        sample_conditions_json=sample_conditions_json,
    )


def _parse_task_config(config_raw: Any) -> dict[str, Any]:
    if not config_raw:
        return {}
    try:
        data = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_preset_catalog(
    db: Session,
    selected_presets: list,
    selected_label_items: list,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    preset_rows: list[dict[str, Any]] = []
    preset_items_by_slug: dict[str, list[dict[str, Any]]] = {}
    preset_meta_by_slug: dict[str, dict[str, Any]] = {}
    seen_preset_slugs: OrderedDict[str, None] = OrderedDict()

    for item in selected_presets:
        slug = str(item.preset_slug or "").strip()
        if slug and slug not in seen_preset_slugs:
            seen_preset_slugs[slug] = None

    for slug in seen_preset_slugs.keys():
        preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
        if not preset:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"preset_slug={slug} 不存在",
            )
        profile_config = None
        if preset.training_profile:
            try:
                profile_config = parse_profile_config(preset.training_profile_config)
            except ValueError as exc:
                raise LabelUException(
                    code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    message=f"预设 {preset.slug} 的复合训练配置无效：{exc}",
                )
        preset_rows.append(
            {
                "slug": preset.slug,
                "name": preset.name,
                "training_profile": preset.training_profile,
                "training_profile_config": profile_config,
            }
        )
        items = []
        for row in sorted(list(preset.items or []), key=lambda x: (x.sort_order, x.id)):
            items.append(
                {
                    "label_key": str(row.label_key or "").strip(),
                    "label_value": str(row.label_value or "").strip(),
                }
            )
        preset_items_by_slug[preset.slug] = items
        preset_meta_by_slug[preset.slug] = {
            "name": preset.name,
            "training_profile": preset.training_profile,
            "training_profile_config": profile_config,
        }

    selected_label_rows: list[dict[str, Any]] = []
    merged_classes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    selected_items_by_preset_slug: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(selected_label_items):
        preset_slug = str(item.preset_slug or "").strip()
        label_value = str(item.label_value or "").strip()
        preset_items = preset_items_by_slug.get(preset_slug) or []
        matched = next(
            (x for x in preset_items if x["label_value"] == label_value), None
        )
        if matched is None:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"{preset_slug}/{label_value} 不存在于预设标签项中",
            )
        class_name = matched["label_value"] or matched["label_key"]
        if not looks_like_yolo_class_slug(class_name):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=(
                    f"{preset_slug}/{label_value} 缺少英文 label_value，"
                    "无法写入训练 data.yaml；请在「预设与维度」补全 slug"
                ),
            )
        display_name = f'{preset_meta_by_slug[preset_slug]["name"]}/{matched["label_key"] or class_name}'
        row = {
            "preset_slug": preset_slug,
            "preset_name": preset_meta_by_slug[preset_slug]["name"],
            "label_key": matched["label_key"],
            "label_value": matched["label_value"],
            "display_name": display_name,
            "selected_order": index,
        }
        selected_label_rows.append(row)
        selected_items_by_preset_slug.setdefault(preset_slug, []).append(row)
        if class_name not in merged_classes:
            merged_classes[class_name] = {
                "class_name": class_name,
                "label_key": matched["label_key"],
                "label_value": matched["label_value"],
                "class_order": len(merged_classes),
                "selected_from": [],
            }
        merged_classes[class_name]["selected_from"].append(display_name)

    if not selected_label_rows:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请至少选择一个标签",
        )

    return (
        preset_rows,
        selected_label_rows,
        list(merged_classes.values()),
        selected_items_by_preset_slug,
    )


def _task_matches_preset(
    task,
    preset_slug: str,
    preset_items: list[dict[str, Any]],
    preset_meta: dict[str, Any] | None = None,
) -> bool:
    preset_meta = preset_meta or {}
    if preset_meta.get("training_profile") == PLATE_RECOGNITION_PROFILE:
        return plate_recognition_train.task_matches_plate_profile(
            task,
            preset_meta.get("training_profile_config"),
        )
    cfg = _parse_task_config(task.config)
    if str(cfg.get("defaultRectPresetId") or "").strip() == preset_slug:
        return True
    preset_classes = [
        x["label_value"] or x["label_key"]
        for x in preset_items
        if x["label_value"] or x["label_key"]
    ]
    task_classes = build_yolo_rect_class_names(cfg)
    return bool(preset_classes) and preset_classes == task_classes


def _task_allowed_aliases_for_selected_items(
    task,
    preset_items_by_slug: dict[str, list[dict[str, Any]]],
    selected_items_by_preset_slug: dict[str, list[dict[str, Any]]],
    preset_meta_by_slug: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, str], list[str]]:
    aliases: dict[str, str] = {}
    display_names: list[str] = []
    for preset_slug, selected_items in selected_items_by_preset_slug.items():
        if not _task_matches_preset(
            task,
            preset_slug,
            preset_items_by_slug.get(preset_slug) or [],
            (preset_meta_by_slug or {}).get(preset_slug),
        ):
            continue
        for item in selected_items:
            class_name = item["label_value"] or item["label_key"]
            raw_values = [item["label_value"], item["label_key"]]
            for raw in raw_values:
                raw = str(raw or "").strip()
                if raw:
                    aliases[raw] = class_name
            display_names.append(item["display_name"])
    return aliases, display_names


def _extract_selected_classes_from_sample(
    sample: TaskSample, aliases: dict[str, str]
) -> list[str]:
    parsed = _parse_sample_annotation(sample)
    if not parsed:
        return []
    _, result = parsed
    classes = []
    seen = set()
    for tool_result in (result.get("rectTool") or {}).get("result") or []:
        raw_label = str(tool_result.get("label") or "").strip()
        canonical = aliases.get(raw_label)
        if canonical and canonical not in seen:
            seen.add(canonical)
            classes.append(canonical)
    return classes


def _contract_sample_rect_geometry_ok(
    outer: dict,
    result: dict,
    task_config: dict[str, Any] | str | None = None,
    class_names: list[str] | None = None,
) -> bool:
    """Cheap preview-time bounds check without opening every source image."""

    if not task_config:
        task_config = {"tools": [{"tool": "rectTool", "config": {}}]}
    return bool(
        detection_rects_for_sample(
            outer,
            result,
            task_config,
            class_names or [],
        )
        or is_explicit_negative_sample(outer)
    )


def _complete_contract_sample_rows(
    db: Session,
    *,
    task_id: int,
    contract_id: int,
) -> list[tuple[SampleAnnotationScope | None, TaskSample]]:
    """Read completed samples even when labels were applied after annotation."""
    return (
        db.query(SampleAnnotationScope, TaskSample)
        .select_from(TaskSample)
        .outerjoin(
            SampleAnnotationScope,
            and_(
                SampleAnnotationScope.sample_id == TaskSample.id,
                SampleAnnotationScope.contract_id == int(contract_id),
            ),
        )
        .filter(
            TaskSample.task_id == int(task_id),
            TaskSample.state == SampleState.DONE.value,
            TaskSample.deleted_at.is_(None),
        )
        .order_by(TaskSample.id)
        .all()
    )
    task = db.query(Task).filter(Task.id == int(task_id)).first()
    if task is None or str(task.source_type or "internal") != "third_party":
        return rows
    trainable_ids = {
        int(sample.id)
        for sample in filter_trainable_samples(
            db, task=task, samples=[sample for _scope, sample in rows]
        )
    }
    return [
        (scope, sample) for scope, sample in rows if int(sample.id) in trainable_ids
    ]


def _preview_contract_mode(
    db: Session,
    *,
    current_user: User,
    cmd: PreviewLabelDimensionTrainingTaskCommand,
) -> LabelDimensionTrainingPreviewResponse:
    # Keep direct service callers compatible with the pre-catalog key names as
    # well as the HTTP route, which normalizes the command before dispatch.
    recipe_key = canonical_training_recipe_key(cmd.training_recipe_key)
    runtime_profile = canonical_runtime_profile(cmd.runtime_profile)
    sample_conditions = _validated_sample_conditions(db, cmd.sample_conditions)
    labels = annotation_contract_service.active_labels_by_ids(db, cmd.label_ids)
    label_ids = [int(row.id) for row in labels]
    class_names = [str(row.label_value) for row in labels]
    category_ids = list(cmd.dataset_category_ids or [])
    if (
        cmd.dataset_category_id is not None
        and int(cmd.dataset_category_id) not in category_ids
    ):
        category_ids.append(int(cmd.dataset_category_id))
    if category_ids:
        category_rows = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.id.in_(category_ids),
                DatasetCategory.status == "active",
                DatasetCategory.deleted_at.is_(None),
            )
            .all()
        )
        if {int(row.id) for row in category_rows} != set(category_ids):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=400,
                message="dataset categories unavailable",
            )
    category = None
    if cmd.dataset_category_id is not None:
        category = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.id == cmd.dataset_category_id,
                DatasetCategory.status == "active",
                DatasetCategory.deleted_at.is_(None),
            )
            .first()
        )
        if not category:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=400,
                message="数据集分类不存在或已停用",
            )
    recipe = (
        db.query(TrainingRecipe)
        .filter(
            TrainingRecipe.recipe_key == recipe_key,
            TrainingRecipe.version == cmd.training_recipe_version,
            TrainingRecipe.status == "active",
            TrainingRecipe.deleted_at.is_(None),
        )
        .first()
    )
    if not recipe:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="训练配方不存在或已停用",
        )
    runtime = (
        db.query(RuntimePublishTemplate)
        .filter(
            RuntimePublishTemplate.runtime_profile == runtime_profile,
            RuntimePublishTemplate.interface_version == cmd.runtime_interface_version,
            RuntimePublishTemplate.status == "active",
            RuntimePublishTemplate.deleted_at.is_(None),
        )
        .first()
    )
    if not runtime:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="运行时模板不存在或已停用",
        )

    selected_task_label_map = {
        int(task_id): {int(value) for value in values}
        for task_id, values in (cmd.selected_task_label_ids or {}).items()
    }
    task_query = crud_task.visible_query(
        db, owner_id=current_user.id, list_all_tasks=is_task_superuser(current_user)
    )
    if selected_task_label_map:
        task_query = task_query.filter(Task.media_type == MediaType.IMAGE.value)
        dataset_scope = _effective_dataset_ids(cmd)
        if dataset_scope:
            task_query = task_query.filter(Task.dataset_id.in_(dataset_scope))
        if cmd.selected_task_ids is not None:
            task_query = task_query.filter(Task.id.in_(cmd.selected_task_ids or [-1]))
        if category_ids:
            task_query = task_query.filter(Task.dataset_category_id.in_(category_ids))
        tasks = [
            task
            for task in task_query.all()
            if selected_task_label_map.get(int(task.id))
        ]
    else:
        task_query = (
            task_query.join(
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
                TaskAnnotationLabel.label_id.in_(label_ids),
            )
            .group_by(Task.id)
            .having(
                func.count(func.distinct(TaskAnnotationLabel.label_id))
                == len(label_ids)
            )
        )
        dataset_scope = _effective_dataset_ids(cmd)
        if dataset_scope:
            task_query = task_query.filter(Task.dataset_id.in_(dataset_scope))
        if cmd.selected_task_ids is not None:
            task_query = task_query.filter(Task.id.in_(cmd.selected_task_ids or [-1]))
        if category_ids:
            task_query = task_query.filter(Task.dataset_category_id.in_(category_ids))
        tasks = task_query.all()
    _validate_dataset_scope(
        db,
        current_user=current_user,
        cmd=cmd,
        tasks=tasks,
    )
    eligible_ids = {int(task.id) for task in tasks}
    if cmd.selected_task_ids is not None:
        visible = (
            crud_task.visible_query(
                db,
                owner_id=current_user.id,
                list_all_tasks=is_task_superuser(current_user),
            )
            .filter(Task.id.in_(cmd.selected_task_ids or [-1]))
            .all()
        )
        visible_ids = {int(task.id) for task in visible}
        inaccessible = set(cmd.selected_task_ids) - visible_ids
        if inaccessible:
            raise LabelUException(
                code=ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=403,
                message=f"任务不可访问或不存在: {sorted(inaccessible)}",
            )

    class_image_counts = {name: 0 for name in class_names}
    sample_condition_option_counts: dict[str, dict[str, int]] = {}
    included_tasks = []
    ignored_tasks = []
    positive_total = negative_total = instance_total = invalid_total = 0
    selected_set = set(label_ids)
    labels_by_id = {int(row.id): row for row in labels}
    for task in tasks:
        contract = annotation_contract_service.current_contract(db, int(task.id))
        task_selected_ids = selected_task_label_map.get(
            int(task.id), selected_set
        ).intersection(selected_set)
        task_class_names = {
            str(labels_by_id[label_id].label_value)
            for label_id in task_selected_ids
            if label_id in labels_by_id
        }
        if contract is None or not task_selected_ids:
            continue
        task_config = parse_task_config(task.config)
        complete_rows = _complete_contract_sample_rows(
            db,
            task_id=int(task.id),
            contract_id=int(contract.id),
        )
        positive = negative = instances = invalid = matched_done = 0
        for _scope, sample in complete_rows:
            parsed = _parse_sample_annotation(sample)
            if not parsed:
                invalid += 1
                continue
            outer, result = parsed
            detection_rects = detection_rects_for_sample(
                outer,
                result,
                task_config,
                task_class_names,
            )
            geometry_ok = bool(detection_rects or is_explicit_negative_sample(outer))
            base_hits = {
                str((rect or {}).get("label") or "").strip() for rect in detection_rects
            }
            if geometry_ok and (base_hits or is_explicit_negative_sample(outer)):
                _increment_sample_condition_counts(
                    sample_condition_option_counts, result
                )
            condition_match, _matched_tags = _sample_matches_conditions(
                result, sample_conditions
            )
            if not condition_match:
                continue
            matched_done += 1
            if not geometry_ok:
                invalid += 1
                continue
            hits = [
                str((rect or {}).get("label") or "").strip() for rect in detection_rects
            ]
            if hits:
                positive += 1
                instances += len(hits)
                for value in set(hits):
                    class_image_counts[value] += 1
                continue
            if is_explicit_negative_sample(parsed[0]):
                negative += 1
            else:
                invalid += 1
        included = positive + negative
        if included:
            included_tasks.append(
                TrainingTaskIncludedTaskResponse(
                    task_id=task.id,
                    task_name=task.name or "",
                    included_image_count=included,
                    matched_done_sample_count=(
                        matched_done if sample_conditions else len(complete_rows)
                    ),
                    match_sources=sorted(task_class_names),
                    positive_image_count=positive,
                    negative_image_count=negative,
                    instance_count=instances,
                    invalid_image_count=invalid,
                    contract_id=int(contract.id),
                    contract_revision=int(contract.revision),
                    label_ids=sorted(task_selected_ids),
                )
            )
            positive_total += positive
            negative_total += negative
            instance_total += instances
            invalid_total += invalid
        else:
            no_condition_match = bool(sample_conditions) and matched_done == 0
            ignored_tasks.append(
                TrainingTaskIgnoredTaskResponse(
                    task_id=task.id,
                    task_name=task.name or "",
                    reason_code=(
                        "NO_SAMPLES_MATCH_CONDITIONS"
                        if no_condition_match
                        else "NO_COMPLETE_SCOPE"
                    ),
                    reason_text=(
                        "所选样本筛选条件未匹配到任何已完成图片"
                        if no_condition_match
                        else "没有与当前标签契约一致的完整样本"
                    ),
                )
            )
    if cmd.selected_task_ids is not None:
        for task_id in sorted(set(cmd.selected_task_ids) - eligible_ids):
            task = db.query(Task).filter(Task.id == task_id).first()
            ignored_tasks.append(
                TrainingTaskIgnoredTaskResponse(
                    task_id=task_id,
                    task_name=(task.name if task else ""),
                    reason_code="LABEL_SCOPE_NOT_COVERED",
                    reason_text="任务标签契约未覆盖全部训练标签",
                )
            )

    if category is None and tasks:
        source_category_ids = {task.dataset_category_id for task in tasks}
        if len(source_category_ids) == 1 and None not in source_category_ids:
            inferred = (
                db.query(DatasetCategory)
                .filter(
                    DatasetCategory.id == next(iter(source_category_ids)),
                    DatasetCategory.status == "active",
                    DatasetCategory.deleted_at.is_(None),
                )
                .first()
            )
            if inferred is not None:
                category = inferred

    merged = [
        MergedTrainingClassResponse(
            class_name=row.label_value,
            label_key=row.label_key,
            label_value=row.label_value,
            class_order=index,
            selected_from=["global_label_library"],
            image_count=class_image_counts[row.label_value],
        )
        for index, row in enumerate(labels)
    ]
    return LabelDimensionTrainingPreviewResponse(
        selected_preset_count=0,
        selected_label_item_count=len(labels),
        merged_class_count=len(labels),
        included_task_count=len(included_tasks),
        included_image_count=positive_total + negative_total,
        ignored_task_count=len(ignored_tasks),
        positive_image_count=positive_total,
        negative_image_count=negative_total,
        instance_count=instance_total,
        invalid_image_count=invalid_total,
        merged_classes=merged,
        included_tasks=included_tasks,
        ignored_tasks=ignored_tasks,
        selected_label_ids=label_ids,
        label_set_signature=label_set_signature(label_ids),
        class_order_signature=class_order_signature(class_names),
        dataset_category_id=(category.id if category else None),
        training_recipe_key=recipe.recipe_key,
        training_recipe_version=recipe.version,
        runtime_profile=runtime_profile,
        runtime_interface_version=cmd.runtime_interface_version,
        sample_conditions=sample_conditions,
        sample_condition_option_counts=sample_condition_option_counts,
    )


def _resolve_task_dimension_labels(
    db: Session,
    *,
    current_user: User,
    selected_task_ids: list[int] | None,
) -> tuple[int, list[int]]:
    """Derive a training contract from the first selected task.

    Task-first creation consumes every selected task's full current contract.
    Mixing scopes would make known but omitted objects become background, so the
    server requires an identical label set for every source task.
    """
    task_ids = [int(value) for value in (selected_task_ids or [])]
    if not task_ids:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="Task-first training requires at least one annotation task",
        )
    visible_tasks = (
        crud_task.visible_query(
            db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        .filter(Task.id.in_(task_ids), Task.media_type == MediaType.IMAGE.value)
        .all()
    )
    by_id = {int(task.id): task for task in visible_tasks}
    missing = [task_id for task_id in task_ids if task_id not in by_id]
    if missing:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message=f"Tasks are unavailable or not images: {missing}",
        )
    base_task_id = task_ids[0]
    base_contract = annotation_contract_service.current_contract(db, base_task_id)
    if base_contract is None or str(base_contract.status or "") not in (
        annotation_contract_service.CURRENT_CONTRACT_TRAINING_STATUSES
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="The base task has no trainable label contract",
        )
    try:
        base_label_ids = [
            int(value) for value in json.loads(base_contract.label_ids_json or "[]")
        ]
    except (TypeError, ValueError, json.JSONDecodeError):
        base_label_ids = []
    base_labels = annotation_contract_service.active_labels_by_ids(db, base_label_ids)
    expected_set = set(base_label_ids)
    expected_order_signature = str(
        getattr(base_contract, "class_order_signature", "") or ""
    ).strip() or class_order_signature(
        [str(row.label_value) for row in base_labels]
    )
    for task_id in task_ids[1:]:
        contract = annotation_contract_service.current_contract(db, task_id)
        if contract is None or str(contract.status or "") not in (
            annotation_contract_service.CURRENT_CONTRACT_TRAINING_STATUSES
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"Task #{task_id} has no trainable label contract",
            )
        try:
            task_label_id_list = [
                int(value) for value in json.loads(contract.label_ids_json or "[]")
            ]
            task_label_ids = set(task_label_id_list)
        except (TypeError, ValueError, json.JSONDecodeError):
            task_label_id_list = []
            task_label_ids = set()
        if task_label_ids != expected_set:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=(
                    f"Task #{task_id} has a different label set from the base task; "
                    "use label-first creation for a cross-contract dataset"
                ),
            )
        task_labels = annotation_contract_service.active_labels_by_ids(
            db,
            task_label_id_list,
        )
        task_order_signature = str(
            getattr(contract, "class_order_signature", "") or ""
        ).strip() or class_order_signature(
            [str(row.label_value) for row in task_labels]
        )
        if task_order_signature != expected_order_signature:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message=(
                    f"Task #{task_id} 的标签顺序与基础任务不同，不能合并训练；"
                    "请统一标签契约顺序后重试"
                ),
            )
    return base_task_id, base_label_ids


def preview_label_dimension_training_task(
    db: Session,
    *,
    current_user: User,
    cmd: PreviewLabelDimensionTrainingTaskCommand,
) -> LabelDimensionTrainingPreviewResponse:
    cmd = _normalize_training_contract_command(cmd)
    # Validate selected logical datasets up front.  This also verifies access
    # for datasets that currently have no eligible task, so an empty result
    # cannot be mistaken for a valid cross-dataset selection.
    _validate_dataset_scope(db, current_user=current_user, cmd=cmd)
    sample_conditions = _validated_sample_conditions(db, cmd.sample_conditions)
    task_dimension_mode = _is_task_dimension_request(cmd)
    if (
        task_dimension_mode
        and str(cmd.training_recipe_key or "").strip() == PLATE_RECOGNITION_PROFILE
    ):
        cmd = _with_plate_profile_for_task_dimension(db, cmd)
    elif task_dimension_mode:
        _, task_label_ids = _resolve_task_dimension_labels(
            db,
            current_user=current_user,
            selected_task_ids=cmd.selected_task_ids,
        )
        cmd = cmd.model_copy(update={"label_ids": task_label_ids})
    if task_dimension_mode:
        _validate_task_dimension_dataset_binding(
            db,
            current_user=current_user,
            selected_task_ids=cmd.selected_task_ids,
            dataset_id=cmd.dataset_id,
            dataset_ids=cmd.dataset_ids,
        )
    if cmd.label_ids:
        preview = _preview_contract_mode(db, current_user=current_user, cmd=cmd)
        task_ids = [
            int(item.task_id)
            for item in preview.included_tasks
        ] or [int(item) for item in (cmd.selected_task_ids or [])]
        return _decorate_preview_contract(
            preview,
            cmd=cmd,
            mode=(
                TRAINING_TASK_MODE_TASK_DIMENSION
                if task_dimension_mode
                else TRAINING_TASK_MODE_LABEL_DIMENSION
            ),
            task_ids=task_ids,
        )
    (
        preset_rows,
        selected_label_rows,
        merged_class_rows,
        selected_items_by_preset_slug,
    ) = _build_preset_catalog(db, cmd.selected_presets, cmd.selected_label_items)
    preset_items_by_slug = {
        row["slug"]: [
            {
                "label_key": str(item.label_key or "").strip(),
                "label_value": str(item.label_value or "").strip(),
            }
            for item in (
                crud_annotation_taxonomy.label_preset_get_by_slug(db, row["slug"]).items
                or []
            )
        ]
        for row in preset_rows
    }
    preset_meta_by_slug = {
        row["slug"]: {
            "training_profile": row.get("training_profile"),
            "training_profile_config": row.get("training_profile_config"),
        }
        for row in preset_rows
    }
    profiles = {
        str(row.get("training_profile") or "").strip()
        for row in preset_rows
        if str(row.get("training_profile") or "").strip()
    }
    if profiles and (len(profiles) != 1 or len(profiles) != len(preset_rows)):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="复合训练预设不能与普通检测预设混合选择",
        )
    training_profile = next(iter(profiles), None)

    task_query = crud_task.visible_query(
        db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    )
    task_query = task_query.filter_by(media_type=MediaType.IMAGE.value)
    dataset_scope = _effective_dataset_ids(cmd)
    if dataset_scope:
        task_query = task_query.filter(Task.dataset_id.in_(dataset_scope))
    selected_task_ids = cmd.selected_task_ids
    if selected_task_ids is not None:
        if not selected_task_ids:
            tasks = []
        else:
            selected_task_id_set = set(selected_task_ids)
            tasks = [
                task for task in task_query.all() if task.id in selected_task_id_set
            ]
            visible_ids = {task.id for task in tasks}
            missing_ids = [
                task_id for task_id in selected_task_ids if task_id not in visible_ids
            ]
            if missing_ids:
                raise LabelUException(
                    code=ErrorCode.CODE_30001_NO_PERMISSION,
                    status_code=status.HTTP_403_FORBIDDEN,
                    message=f"任务不可访问或不存在：{missing_ids}",
                )
    else:
        tasks = task_query.all()
    _validate_dataset_scope(
        db,
        current_user=current_user,
        cmd=cmd,
        tasks=tasks,
    )
    try:
        # Multi-task training is valid only when every selected task has the
        # same explicit project.  A known and an unscoped legacy task cannot
        # be safely combined because project ownership must not be inferred.
        assert_project_chain(
            [task_project_id(db, task.id) for task in tasks],
            context="training task sources",
        )
    except ProjectScopeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="所选训练任务项目归属不一致，无法合并训练",
        ) from exc
    class_image_counts = {row["class_name"]: 0 for row in merged_class_rows}
    included_tasks: list[TrainingTaskIncludedTaskResponse] = []
    ignored_tasks: list[TrainingTaskIgnoredTaskResponse] = []
    included_image_count = 0
    positive_image_count = 0
    negative_image_count = 0
    instance_count = 0
    invalid_image_count = 0

    if training_profile == PLATE_RECOGNITION_PROFILE:
        if sample_conditions:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="复合车牌训练暂不支持场景条件筛选，请保持原训练范围",
            )
        profile_row = preset_rows[0]
        profile_config = profile_row.get("training_profile_config")
        selected_display_names = [row["display_name"] for row in selected_label_rows]
        for task in tasks:
            if not plate_recognition_train.task_matches_plate_profile(
                task, profile_config
            ):
                continue
            task_audit, _dataset_samples = (
                plate_recognition_train.audit_task_done_samples(
                    db,
                    task_id=task.id,
                    profile_config=profile_config,
                )
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
                task_audit, _dataset_samples = (
                    plate_recognition_train.audit_plate_samples(
                        filter_trainable_samples(db, task=task, samples=done_samples),
                        task_id=int(task.id),
                        profile_config=profile_config,
                    )
                )
            if task_audit.included_image_count:
                included_tasks.append(
                    TrainingTaskIncludedTaskResponse(
                        task_id=task.id,
                        task_name=task.name or "",
                        included_image_count=task_audit.included_image_count,
                        matched_done_sample_count=task_audit.done_sample_count,
                        match_sources=selected_display_names,
                        positive_image_count=task_audit.positive_image_count,
                        negative_image_count=task_audit.negative_image_count,
                        instance_count=task_audit.instance_count,
                        invalid_image_count=task_audit.invalid_image_count,
                    )
                )
                included_image_count += task_audit.included_image_count
                positive_image_count += task_audit.positive_image_count
                negative_image_count += task_audit.negative_image_count
                instance_count += task_audit.instance_count
                invalid_image_count += task_audit.invalid_image_count
            else:
                invalid_image_count += task_audit.invalid_image_count
                ignored_tasks.append(
                    TrainingTaskIgnoredTaskResponse(
                        task_id=task.id,
                        task_name=task.name or "",
                        reason_code=(
                            "INVALID_PLATE_ANNOTATIONS"
                            if task_audit.invalid_image_count
                            else "NO_DONE_SAMPLES"
                        ),
                        reason_text=(
                            f"{task_audit.invalid_image_count} 张已完成图片的车牌标注结构无效"
                            if task_audit.invalid_image_count
                            else "没有可训练的已完成图片"
                        ),
                    )
                )
        merged_classes = [
            MergedTrainingClassResponse(
                class_name=row["class_name"],
                label_key=row.get("label_key"),
                label_value=row.get("label_value"),
                class_order=row["class_order"],
                selected_from=row["selected_from"],
                image_count=positive_image_count,
            )
            for row in merged_class_rows
        ]
        dataset_category = (
            _resolve_plate_dataset_category(
                db,
                cmd=cmd,
                tasks=tasks,
            )
            if task_dimension_mode
            else None
        )
        preview = LabelDimensionTrainingPreviewResponse(
            selected_preset_count=len(preset_rows),
            selected_label_item_count=len(selected_label_rows),
            merged_class_count=len(merged_class_rows),
            included_task_count=len(included_tasks),
            included_image_count=included_image_count,
            ignored_task_count=len(ignored_tasks),
            training_profile=training_profile,
            positive_image_count=positive_image_count,
            negative_image_count=negative_image_count,
            instance_count=instance_count,
            invalid_image_count=invalid_image_count,
            merged_classes=merged_classes,
            included_tasks=included_tasks,
            ignored_tasks=ignored_tasks,
            dataset_category_id=(
                int(dataset_category.id) if dataset_category is not None else None
            ),
            training_recipe_key=(
                cmd.training_recipe_key if task_dimension_mode else None
            ),
            training_recipe_version=(
                cmd.training_recipe_version if task_dimension_mode else None
            ),
            runtime_profile=(cmd.runtime_profile if task_dimension_mode else None),
            runtime_interface_version=(
                cmd.runtime_interface_version if task_dimension_mode else None
            ),
            sample_conditions={},
        )
        return _decorate_preview_contract(
            preview,
            cmd=cmd,
            mode=(
                TRAINING_TASK_MODE_TASK_DIMENSION
                if task_dimension_mode
                else TRAINING_TASK_MODE_LABEL_DIMENSION
            ),
            task_ids=[int(item.task_id) for item in included_tasks]
            or [int(item) for item in (cmd.selected_task_ids or [])],
        )

    for task in tasks:
        aliases, display_names = _task_allowed_aliases_for_selected_items(
            task,
            preset_items_by_slug,
            selected_items_by_preset_slug,
            preset_meta_by_slug,
        )
        if not aliases:
            continue
        samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == task.id,
                TaskSample.state == SampleState.DONE.value,
                TaskSample.deleted_at.is_(None),
            )
            .order_by(TaskSample.id.asc())
            .all()
        )
        samples = filter_trainable_samples(db, task=task, samples=samples)
        if sample_conditions:
            filtered_samples = []
            for sample in samples:
                parsed = _parse_sample_annotation(sample)
                if not parsed:
                    continue
                _outer, result = parsed
                condition_match, _matched_tags = _sample_matches_conditions(
                    result, sample_conditions
                )
                if condition_match:
                    filtered_samples.append(sample)
            samples = filtered_samples
        matched_done = len(samples)
        included_for_task = 0
        for sample in samples:
            hit_classes = _extract_selected_classes_from_sample(sample, aliases)
            if not hit_classes:
                continue
            included_for_task += 1
            included_image_count += 1
            for class_name in hit_classes:
                class_image_counts[class_name] = (
                    class_image_counts.get(class_name, 0) + 1
                )
        if included_for_task > 0:
            included_tasks.append(
                TrainingTaskIncludedTaskResponse(
                    task_id=task.id,
                    task_name=task.name or "",
                    included_image_count=included_for_task,
                    matched_done_sample_count=matched_done,
                    match_sources=display_names,
                )
            )
        else:
            no_condition_match = bool(sample_conditions) and not samples
            ignored_tasks.append(
                TrainingTaskIgnoredTaskResponse(
                    task_id=task.id,
                    task_name=task.name or "",
                    reason_code=(
                        "NO_SAMPLES_MATCH_CONDITIONS"
                        if no_condition_match
                        else "NO_VALID_SAMPLES_AFTER_CLEAN"
                    ),
                    reason_text=(
                        "所选样本筛选条件未匹配到任何已完成图片"
                        if no_condition_match
                        else "按标签清洗后无有效图片"
                    ),
                )
            )

    merged_classes = [
        MergedTrainingClassResponse(
            class_name=row["class_name"],
            label_key=row.get("label_key"),
            label_value=row.get("label_value"),
            class_order=row["class_order"],
            selected_from=row["selected_from"],
            image_count=class_image_counts.get(row["class_name"], 0),
        )
        for row in merged_class_rows
    ]
    preview = LabelDimensionTrainingPreviewResponse(
        selected_preset_count=len(preset_rows),
        selected_label_item_count=len(selected_label_rows),
        merged_class_count=len(merged_class_rows),
        included_task_count=len(included_tasks),
        included_image_count=included_image_count,
        ignored_task_count=len(ignored_tasks),
        training_profile=training_profile,
        positive_image_count=included_image_count,
        merged_classes=merged_classes,
        included_tasks=included_tasks,
        ignored_tasks=ignored_tasks,
        sample_conditions=sample_conditions,
    )
    return _decorate_preview_contract(
        preview,
        cmd=cmd,
        mode=(
            TRAINING_TASK_MODE_TASK_DIMENSION
            if task_dimension_mode
            else TRAINING_TASK_MODE_LABEL_DIMENSION
        ),
        task_ids=[int(item.task_id) for item in included_tasks]
        or [int(item) for item in (cmd.selected_task_ids or [])],
    )


def _preview_summary_dict(
    preview: LabelDimensionTrainingPreviewResponse,
) -> dict[str, Any]:
    return {
        "selected_preset_count": preview.selected_preset_count,
        "selected_label_item_count": preview.selected_label_item_count,
        "merged_class_count": preview.merged_class_count,
        "included_task_count": preview.included_task_count,
        "included_image_count": preview.included_image_count,
        "ignored_task_count": preview.ignored_task_count,
        "training_profile": preview.training_profile,
        "positive_image_count": preview.positive_image_count,
        "negative_image_count": preview.negative_image_count,
        "instance_count": preview.instance_count,
        "invalid_image_count": preview.invalid_image_count,
        "sample_conditions": preview.sample_conditions,
        "branch_key": preview.branch_key,
        "selection_contract_sha256": preview.selection_contract_sha256,
        "selection_contract": preview.selection_contract,
        "class_image_stats": [
            {"class_name": item.class_name, "image_count": item.image_count or 0}
            for item in preview.merged_classes
        ],
    }


def _training_task_replay_response(
    training_task: Any,
    job: Any,
) -> TrainingTaskCreateResponse:
    """Rebuild the create response without running preview or dispatch again."""
    try:
        summary = json.loads(training_task.preview_summary_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        summary = {}
    return TrainingTaskCreateResponse(
        training_task_id=int(training_task.id),
        name=str(training_task.name),
        mode=str(training_task.mode),
        primary_task_id=(
            int(training_task.primary_task_id)
            if training_task.primary_task_id is not None
            else None
        ),
        latest_job_id=int(job.id),
        series_id=(
            int(getattr(training_task, "series_id", None))
            if getattr(training_task, "series_id", None) is not None
            else None
        ),
        variant_id=(
            int(getattr(training_task, "variant_id", None))
            if getattr(training_task, "variant_id", None) is not None
            else None
        ),
        branch_key=getattr(training_task, "branch_key", None),
        selection_contract_sha256=getattr(
            training_task, "selection_contract_sha256", None
        ),
        included_task_count=int(summary.get("included_task_count") or 0),
        included_image_count=int(summary.get("included_image_count") or 0),
        ignored_task_count=int(summary.get("ignored_task_count") or 0),
        message="已复用原训练任务，未重复创建",
    )


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _freeze_contract_samples(
    db: Session,
    *,
    included_task_ids: list[int],
    selected_label_ids: list[int],
    class_names: list[str],
    sample_conditions: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    conditions = _validated_sample_conditions(db, sample_conditions)
    selected_set = set(selected_label_ids)
    manifest = []
    for task_id in included_task_ids:
        contract = annotation_contract_service.current_contract(db, task_id)
        if contract is None:
            continue
        task = crud_task.get(db, task_id=int(task_id))
        task_config = parse_task_config(task.config if task else {})
        rows = _complete_contract_sample_rows(
            db,
            task_id=int(task_id),
            contract_id=int(contract.id),
        )
        contract_label_ids = set(json.loads(contract.label_ids_json or "[]"))
        for _scope, sample in rows:
            parsed = _parse_sample_annotation(sample)
            if not parsed:
                continue
            outer, result = parsed
            condition_match, _matched_tags = _sample_matches_conditions(
                result, conditions
            )
            if not condition_match:
                continue
            selected_rects = detection_rects_for_sample(
                outer,
                result,
                task_config,
                class_names,
            )
            if not selected_rects and not is_explicit_negative_sample(outer):
                continue
            current_annotation_sha256 = annotation_sha256(outer)
            explicit_negative = is_explicit_negative_sample(outer)
            negative_ids = contract_label_ids if explicit_negative else set()
            if not selected_rects and not (
                explicit_negative and selected_set.issubset(negative_ids)
            ):
                continue
            from labelu.internal.application.service.attachment import (
                resolve_attachment_local_path,
            )

            file_path = resolve_attachment_local_path(sample.file)
            current_image_sha256 = _file_sha256(file_path) if file_path else None
            if not current_image_sha256:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message=f"样本 #{sample.id} 的源图片不存在或不可读取，无法冻结训练数据集",
                )
            manifest.append(
                {
                    "source_task_id": int(task_id),
                    "source_sample_id": int(sample.id),
                    "contract_id": int(contract.id),
                    "contract_revision": int(contract.revision),
                    "annotation_sha256": current_annotation_sha256,
                    "image_sha256": current_image_sha256,
                    "negative_label_ids": sorted(negative_ids),
                }
            )
    return manifest


def _message_for_create(
    preview: LabelDimensionTrainingPreviewResponse,
    *,
    plate_training_target: str | None = None,
) -> str:
    if preview.training_profile == PLATE_RECOGNITION_PROFILE:
        target_label = (
            "车牌检测"
            if plate_training_target == plate_recognition_train.PLATE_DETECTION_JOB_TYPE
            else "完整车牌识别"
        )
        return (
            f"{target_label}训练任务已创建。纳入任务 {preview.included_task_count} 个，"
            f"正样本 {preview.positive_image_count} 张，负样本 {preview.negative_image_count} 张，"
            f"车牌实例 {preview.instance_count} 个。"
        )
    return (
        f"训练任务已创建。纳入任务 {preview.included_task_count} 个，"
        f"纳入图片 {preview.included_image_count} 张，忽略任务 {preview.ignored_task_count} 个。"
    )


def _validate_plate_recognition_weights(
    cmd: CreateLabelDimensionTrainingTaskCommand,
) -> None:
    source = str(cmd.weights_source or "preset").strip()
    selected = str(cmd.model_weights or "").strip()
    expected = plate_recognition_train.PLATE_POSE_PRETRAINED_MODEL
    if source != "preset" or cmd.weights_ref_id is not None or selected != expected:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                "车牌复合训练的 Pose 组件必须在前端明确选择 "
                f"{expected}，不支持静默替换或使用其他权重"
            ),
        )
    try:
        plate_recognition_train.resolve_plate_pose_pretrained_model(selected)
    except RuntimeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"车牌复合训练无法开始：{exc}",
        ) from exc


def _validate_plate_iteration_source(
    db: Session,
    *,
    current_user: User,
    source_job_id: int,
    expected_job_type: str,
):
    source_job = crud_yolo_train_job.get(db, int(source_job_id))
    if not source_job or int(source_job.created_by or 0) != int(current_user.id):
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="plate iteration source job not found",
        )
    if str(source_job.job_type or "").strip() != str(expected_job_type).strip():
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="迭代来源的训练目标与当前车牌训练目标不一致",
        )
    try:
        if expected_job_type == plate_recognition_train.PLATE_DETECTION_JOB_TYPE:
            plate_recognition_train.load_plate_detection_iteration_artifact(
                db, int(source_job_id)
            )
        else:
            plate_recognition_train.load_plate_iteration_component_artifacts(
                db, int(source_job_id)
            )
    except RuntimeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"plate iteration source is unavailable: {exc}",
        ) from exc
    return source_job


def _enqueue_label_dimension_train_job(
    job_id: int, task_id: int, cmd: CreateLabelDimensionTrainingTaskCommand
) -> None:
    from labelu.internal.application.service import yolo_train as yolo_train_service

    train_cmd = CreateYoloTrainJobCommand(
        model_weights=cmd.model_weights,
        weights_source=cmd.weights_source,
        weights_ref_id=cmd.weights_ref_id,
        epochs=cmd.epochs,
        imgsz=cmd.imgsz,
        category=cmd.name,
        hyperparams=cmd.hyperparams,
    )
    yolo_train_service._submit_yolo_train_job(job_id, task_id, train_cmd)


def _enqueue_plate_recognition_train_job(
    job_id: int,
    task_id: int,
    cmd: CreateLabelDimensionTrainingTaskCommand,
) -> None:
    plate_recognition_train.submit_plate_recognition_train_job(
        job_id=job_id,
        task_id=task_id,
        model_weights=cmd.model_weights,
        epochs=cmd.epochs,
        imgsz=cmd.imgsz,
        hyperparams=cmd.hyperparams,
    )


def _build_label_dimension_task_config(
    merged_class_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    attributes = []
    for item in sorted(
        merged_class_rows,
        key=lambda row: int(row.get("class_order", 0) or 0),
    ):
        label_value = str(
            item.get("label_value") or item.get("class_name") or ""
        ).strip()
        label_key = str(item.get("label_key") or label_value).strip()
        if not label_value:
            continue
        attribute = {"key": label_key, "value": label_value}
        if item.get("label_id") is not None:
            attribute["labelId"] = int(item["label_id"])
        attributes.append(attribute)
    return {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": attributes},
            }
        ]
    }


def _aliases_for_source_task(
    selected_label_rows: list[dict[str, Any]],
    source_task_snapshot: dict[str, Any],
) -> dict[str, str]:
    match_sources = {
        str(item or "").strip()
        for item in (source_task_snapshot.get("match_sources") or [])
        if str(item or "").strip()
    }
    aliases: dict[str, str] = {}
    for item in selected_label_rows:
        display_name = str(item.get("display_name") or "").strip()
        if match_sources and display_name not in match_sources:
            continue
        class_name = str(item.get("label_value") or item.get("label_key") or "").strip()
        if not class_name:
            continue
        for raw in (item.get("label_value"), item.get("label_key")):
            raw_label = str(raw or "").strip()
            if raw_label:
                aliases[raw_label] = class_name
    return aliases


def _clone_sample_with_cleaned_rects(
    sample: TaskSample,
    aliases: dict[str, str],
    task_config: dict[str, Any] | str | None = None,
    class_names: list[str] | None = None,
) -> TaskSample | None:
    parsed = _parse_sample_annotation(sample)
    if not parsed:
        return None
    outer, result = parsed
    cleaned_rects = detection_rects_for_sample(
        outer,
        result,
        task_config or {"tools": [{"tool": "rectTool", "config": {}}]},
        class_names or aliases.values(),
        aliases=aliases,
    )
    if not cleaned_rects and not is_explicit_negative_sample(outer):
        return None

    cleaned_result = dict(result)
    cleaned_rect_block = dict(result.get("rectTool") or {})
    cleaned_rect_block["result"] = cleaned_rects
    cleaned_result["rectTool"] = cleaned_rect_block

    cleaned_outer = dict(outer)
    cleaned_outer["result"] = json.dumps(cleaned_result, ensure_ascii=False)

    cloned = TaskSample()
    cloned.id = sample.id
    cloned.task_id = sample.task_id
    cloned.state = sample.state
    cloned.file = sample.file
    cloned.data = json.dumps(cleaned_outer, ensure_ascii=False)
    return cloned


def build_label_dimension_dataset_context(
    db: Session,
    *,
    current_user: User,
    training_task_id: int,
) -> tuple[dict[str, Any], list[TaskSample], dict[int, int], list[int]]:
    row = crud_training_task.get(db, training_task_id)
    if not row or row.owner_id != current_user.id:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="training task not found",
        )

    selected_label_ids = crud_training_task.parse_snapshot(row.selected_label_ids_json)
    source_sample_snapshot = crud_training_task.parse_snapshot(
        row.source_sample_snapshot_json
    )
    if selected_label_ids and source_sample_snapshot:
        class_names = crud_training_task.parse_snapshot(row.class_names_snapshot_json)
        labels = annotation_contract_service.active_labels_by_ids(
            db, selected_label_ids, allow_disabled=True
        )
        merged = [
            {
                "label_id": label.id,
                "label_key": label.label_key,
                "label_value": label.label_value,
                "class_name": class_names[index],
                "class_order": index,
            }
            for index, label in enumerate(labels)
        ]
        task_config = _build_label_dimension_task_config(merged)
        aliases = {str(name): str(name) for name in class_names}
        samples = []
        per_task_counts: dict[int, int] = {}
        source_task_ids = []
        for item in source_sample_snapshot:
            task_id = int(item["source_task_id"])
            sample_id = int(item["source_sample_id"])
            if task_id not in source_task_ids:
                source_task_ids.append(task_id)
            sample = (
                db.query(TaskSample)
                .filter(
                    TaskSample.id == sample_id,
                    TaskSample.task_id == task_id,
                    TaskSample.deleted_at.is_(None),
                )
                .first()
            )
            current_contract = annotation_contract_service.current_contract(db, task_id)
            parsed = _parse_sample_annotation(sample) if sample else None
            current_annotation_sha256 = annotation_sha256(parsed[0]) if parsed else None
            from labelu.internal.application.service.attachment import (
                resolve_attachment_local_path,
            )

            file_path = resolve_attachment_local_path(sample.file if sample else None)
            current_image_sha256 = _file_sha256(file_path) if file_path else None
            if (
                not sample
                or sample.state != SampleState.DONE.value
                or not current_contract
                or int(current_contract.id) != int(item["contract_id"])
                or not parsed
                or str(current_annotation_sha256 or "")
                != str(item.get("annotation_sha256") or "")
                or not item.get("image_sha256")
                or str(current_image_sha256 or "")
                != str(item.get("image_sha256") or "")
            ):
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message=f"冻结样本 #{sample_id} 已变化，禁止静默重建数据集",
                )
            source_task = crud_task.get(db, task_id=task_id)
            cleaned = _clone_sample_with_cleaned_rects(
                sample,
                aliases,
                task_config=parse_task_config(
                    source_task.config if source_task else {}
                ),
                class_names=class_names,
            )
            if cleaned is not None:
                samples.append(cleaned)
                per_task_counts[task_id] = per_task_counts.get(task_id, 0) + 1
        if not samples:
            raise LabelUException(
                code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
                status_code=400,
                message="冻结样本不可用",
            )
        return task_config, samples, per_task_counts, source_task_ids

    selected_label_rows = sorted(
        crud_training_task.parse_snapshot(row.selected_label_items_snapshot_json),
        key=lambda item: int(item.get("selected_order", 0) or 0),
    )
    merged_class_rows = crud_training_task.parse_snapshot(
        row.merged_class_names_snapshot_json
    )
    source_task_snapshots = crud_training_task.parse_snapshot(
        row.source_tasks_snapshot_json
    )
    source_task_ids = sorted(
        {
            int(item.get("task_id"))
            for item in source_task_snapshots
            if item.get("task_id") is not None
        }
    )
    source_task_snapshot_by_id = {
        int(item.get("task_id")): item
        for item in source_task_snapshots
        if item.get("task_id") is not None
    }

    task_config = _build_label_dimension_task_config(merged_class_rows)
    samples: list[TaskSample] = []
    per_task_counts: dict[int, int] = {}
    for source_task_id in source_task_ids:
        task = crud_task.get(db, task_id=source_task_id)
        if not task:
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
                message=f"task #{source_task_id} not found",
            )
        aliases = _aliases_for_source_task(
            selected_label_rows,
            source_task_snapshot_by_id.get(source_task_id) or {},
        )
        if not aliases:
            continue
        task_samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == source_task_id,
                TaskSample.state == SampleState.DONE.value,
                TaskSample.deleted_at.is_(None),
            )
            .order_by(TaskSample.id.asc())
            .all()
        )
        per_task_counts[source_task_id] = len(task_samples)
        for sample in task_samples:
            cleaned = _clone_sample_with_cleaned_rects(
                sample,
                aliases,
                task_config=parse_task_config(task.config),
                class_names=[
                    str(item.get("label_value") or item.get("label_key") or "")
                    for item in merged_class_rows
                ],
            )
            if cleaned is not None:
                samples.append(cleaned)

    if not samples:
        raise LabelUException(
            code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="no usable cleaned samples",
        )
    return task_config, samples, per_task_counts, source_task_ids


def create_label_dimension_training_task(
    db: Session,
    *,
    current_user: User,
    cmd: CreateLabelDimensionTrainingTaskCommand,
    idempotency_key: str | None = None,
) -> TrainingTaskCreateResponse:
    # Normalize before hashing an idempotent request so the legacy alias and
    # the canonical catalog key describe the same immutable job contract.
    cmd = _normalize_training_contract_command(cmd)
    retry_key = normalize_idempotency_key(idempotency_key)
    if retry_key and len(retry_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="Idempotency-Key 不能超过 256 个字符",
        )
    retry_hash = (
        request_payload_hash(
            {"route": "yolo-training-tasks", "command": cmd.dict()}
        )
        if retry_key
        else None
    )
    if retry_key:
        existing_job = crud_yolo_train_job.get_by_idempotency(
            db, creator_id=current_user.id, idempotency_key=retry_key
        )
        if existing_job is not None:
            if existing_job.idempotency_request_hash != retry_hash:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="Idempotency-Key 已用于其他训练请求",
                )
            existing_task = (
                crud_training_task.get(db, training_task_id=int(existing_job.training_task_id))
                if existing_job.training_task_id
                else None
            )
            if existing_task is None:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="幂等训练作业缺少关联训练任务，请联系管理员",
                )
            return _training_task_replay_response(existing_task, existing_job)
    normalized_name = _validate_name(cmd.name)
    if not check_training_task_name_available(db, current_user.id, normalized_name):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=_DUPLICATE_NAME_MESSAGE,
        )

    task_dimension_mode = _is_task_dimension_request(cmd)
    if (
        task_dimension_mode
        and str(cmd.training_recipe_key or "").strip() == PLATE_RECOGNITION_PROFILE
    ):
        cmd = _with_plate_profile_for_task_dimension(db, cmd)
    elif task_dimension_mode:
        _, derived_label_ids = _resolve_task_dimension_labels(
            db,
            current_user=current_user,
            selected_task_ids=cmd.selected_task_ids,
        )
        cmd = cmd.model_copy(update={"label_ids": derived_label_ids})

    preview_cmd = PreviewLabelDimensionTrainingTaskCommand(
        selected_presets=cmd.selected_presets,
        selected_label_items=cmd.selected_label_items,
        selected_task_ids=cmd.selected_task_ids,
        dataset_id=cmd.dataset_id,
        dataset_ids=cmd.dataset_ids,
        label_ids=cmd.label_ids,
        dataset_category_id=cmd.dataset_category_id,
        dataset_category_ids=cmd.dataset_category_ids,
        selected_task_label_ids=cmd.selected_task_label_ids,
        training_recipe_key=cmd.training_recipe_key,
        training_recipe_version=cmd.training_recipe_version,
        runtime_profile=cmd.runtime_profile,
        runtime_interface_version=cmd.runtime_interface_version,
        sample_conditions=cmd.sample_conditions,
        series_id=cmd.series_id,
        variant_id=cmd.variant_id,
        series_name=cmd.series_name,
        variant_name=cmd.variant_name,
        model_family=cmd.model_family,
        parent_model_version_id=cmd.parent_model_version_id,
        parent_training_job_id=cmd.parent_training_job_id,
        branch_key=cmd.branch_key,
        inheritance_mode=cmd.inheritance_mode,
        selection_contract=cmd.selection_contract,
    )
    preview = preview_label_dimension_training_task(
        db, current_user=current_user, cmd=preview_cmd
    )
    # The preview is the authoritative candidate snapshot for this request.
    # This applies to both task-first and label-first creation: never silently
    # replace a user's explicit multi-select with only the tasks that still
    # happen to contain usable samples after a refresh or concurrent update.
    _validate_selected_tasks_in_preview(
        selected_task_ids=cmd.selected_task_ids,
        preview=preview,
    )
    if preview.included_image_count < 1:
        raise LabelUException(
            code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前所选标签没有可训练图片",
        )

    contract_mode = bool(cmd.label_ids)
    if contract_mode:
        canonical_labels = annotation_contract_service.active_labels_by_ids(
            db, cmd.label_ids
        )
        preset_rows = []
        selected_label_rows = [
            {
                "label_id": row.id,
                "preset_slug": "",
                "label_key": row.label_key,
                "label_value": row.label_value,
                "display_name": row.label_key,
                "selected_order": index,
            }
            for index, row in enumerate(canonical_labels)
        ]
        merged_class_rows = [
            {
                "class_name": row.label_value,
                "label_id": row.id,
                "label_key": row.label_key,
                "label_value": row.label_value,
                "class_order": index,
                "selected_from": ["global_label_library"],
            }
            for index, row in enumerate(canonical_labels)
        ]
    else:
        (
            preset_rows,
            selected_label_rows,
            merged_class_rows,
            _selected_items_by_preset_slug,
        ) = _build_preset_catalog(db, cmd.selected_presets, cmd.selected_label_items)
    primary_task_id = (
        int(cmd.selected_task_ids[0])
        if task_dimension_mode and cmd.selected_task_ids
        else min(item.task_id for item in preview.included_tasks)
    )
    source_task_ids = [item.task_id for item in preview.included_tasks]
    source_task_rows = (
        db.query(Task).filter(Task.id.in_(source_task_ids or [-1])).all()
    )
    lineage_dataset_id, lineage_dataset_ids = _validate_dataset_scope(
        db,
        current_user=current_user,
        cmd=cmd,
        tasks=source_task_rows,
    )
    is_plate_profile = (
        not contract_mode
    ) and preview.training_profile == PLATE_RECOGNITION_PROFILE
    is_plate_task_dimension = is_plate_profile and task_dimension_mode
    if not is_plate_profile and cmd.plate_training_target is not None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="plate_training_target 仅适用于车牌标注预设",
        )
    plate_training_target = (
        str(cmd.plate_training_target or "").strip()
        or plate_recognition_train.PLATE_RECOGNITION_JOB_TYPE
        if is_plate_profile
        else None
    )
    training_mode = (
        (
            plate_recognition_train.PLATE_DETECTION_TRAINING_MODE
            if plate_training_target == plate_recognition_train.PLATE_DETECTION_JOB_TYPE
            else plate_recognition_train.PLATE_RECOGNITION_TRAINING_MODE
        )
        if is_plate_profile
        else (
            TRAINING_TASK_MODE_TASK_DIMENSION
            if task_dimension_mode
            else TRAINING_TASK_MODE_LABEL_DIMENSION
        )
    )
    recipe = None
    category_row = None
    plate_dataset_category = None
    if is_plate_task_dimension:
        source_tasks = db.query(Task).filter(Task.id.in_(source_task_ids or [-1])).all()
        plate_dataset_category = _resolve_plate_dataset_category(
            db,
            cmd=cmd,
            tasks=source_tasks,
        )
    effective_dataset_category_id = (
        int(plate_dataset_category.id)
        if plate_dataset_category is not None
        else preview.dataset_category_id
    )
    if contract_mode:
        recipe = (
            db.query(TrainingRecipe)
            .filter(
                TrainingRecipe.recipe_key == cmd.training_recipe_key,
                TrainingRecipe.version == cmd.training_recipe_version,
            )
            .first()
        )
    if (
        contract_mode or is_plate_task_dimension
    ) and effective_dataset_category_id is not None:
        category_row = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.id == effective_dataset_category_id,
                DatasetCategory.status == "active",
                DatasetCategory.deleted_at.is_(None),
            )
            .first()
        )
        if category_row is None:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=400,
                message="数据集分类不存在或已停用",
            )
    job_type = (
        plate_training_target
        if is_plate_profile
        else (recipe.job_type if recipe else "yolo_detection")
    )
    # The registry may contain second-phase recipes, but they cannot create
    # an executable training job until their real worker/toolchain is enabled.
    job_type = ensure_phase_one_training_job_type(job_type)
    component_status = (
        plate_recognition_train.initial_component_status(
            plate_recognition_train.PLATE_DETECTION_COMPONENTS
            if job_type == plate_recognition_train.PLATE_DETECTION_JOB_TYPE
            else plate_recognition_train.PLATE_COMPONENTS
        )
        if plate_recognition_train.is_plate_job_type(job_type)
        else None
    )
    effective_cmd = cmd
    if plate_recognition_train.is_plate_job_type(job_type):
        _validate_plate_recognition_weights(cmd)
        effective_hyperparams = dict(cmd.hyperparams or {})
        effective_hyperparams.pop(
            plate_recognition_train.PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM,
            None,
        )
        if cmd.iteration_source_job_id is not None:
            _validate_plate_iteration_source(
                db,
                current_user=current_user,
                source_job_id=cmd.iteration_source_job_id,
                expected_job_type=job_type,
            )
            effective_hyperparams[
                plate_recognition_train.PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM
            ] = int(cmd.iteration_source_job_id)
        effective_cmd = cmd.model_copy(update={"hyperparams": effective_hyperparams})
    elif cmd.iteration_source_job_id is not None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="iteration_source_job_id is only supported for plate recognition",
        )
    else:
        # Queue recovery resolves the persisted model_weights token. Freeze
        # imported/trained references so an iteration cannot fall back to the
        # display-only preset name after submission or a service restart.
        from labelu.internal.application.service import yolo_train as yolo_train_service

        train_cmd = CreateYoloTrainJobCommand(
            model_weights=cmd.model_weights,
            weights_source=cmd.weights_source,
            weights_ref_id=cmd.weights_ref_id,
            training_recipe_key=cmd.training_recipe_key,
            training_recipe_version=cmd.training_recipe_version,
        )
        expected_label_set_signature = (
            label_set_signature([int(row.id) for row in canonical_labels])
            if contract_mode
            else None
        )
        expected_class_order_signature = (
            class_order_signature([str(row.label_value) for row in canonical_labels])
            if contract_mode
            else None
        )
        if train_cmd.weights_source == "auto":
            from labelu.internal.application.service.training_iteration import (
                resolve_auto_iteration_weights,
            )

            train_cmd = resolve_auto_iteration_weights(
                db,
                current_user=current_user,
                task_id=primary_task_id,
                cmd=train_cmd,
                source_task_ids=source_task_ids,
                expected_label_set_signature=expected_label_set_signature,
                expected_class_order_signature=expected_class_order_signature,
            )
        if train_cmd.weights_source == "trained" and train_cmd.weights_ref_id:
            # Preserve the legacy resolver's missing-row error, while making
            # existing explicit parents pass the same lineage gate as preview.
            parent_row = crud_yolo_train_job.get(
                db=db, job_id=int(train_cmd.weights_ref_id)
            )
            if parent_row is not None:
                from labelu.internal.application.service.training_iteration import (
                    validate_explicit_iteration_parent,
                )

                validate_explicit_iteration_parent(
                    db,
                    current_user=current_user,
                    parent_job_id=int(train_cmd.weights_ref_id),
                    task_id=primary_task_id,
                    requested_recipe_key=train_cmd.training_recipe_key,
                    requested_recipe_version=train_cmd.training_recipe_version,
                    expected_label_set_signature=expected_label_set_signature,
                    expected_class_order_signature=expected_class_order_signature,
                )
        yolo_train_service._validate_weights_source_at_create(
            db, current_user, train_cmd
        )
        stored_weights = yolo_train_service._model_weights_field_for_storage(train_cmd)
        effective_cmd = cmd.model_copy(
            update={
                "model_weights": stored_weights,
                "weights_source": None,
                "weights_ref_id": None,
            }
        )
    source_sample_snapshot = (
        _freeze_contract_samples(
            db,
            included_task_ids=source_task_ids,
            selected_label_ids=[int(row.id) for row in canonical_labels],
            class_names=[str(row.label_value) for row in canonical_labels],
            sample_conditions=cmd.sample_conditions,
        )
        if contract_mode
        else []
    )
    if contract_mode and not source_sample_snapshot:
        raise LabelUException(
            code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
            status_code=400,
            message="没有可冻结的完整样本",
        )

    # The legacy label/task-dimension endpoint used to create a training job
    # without the immutable dataset lineage required by the model registry.
    # Create the same frozen version used by the unified endpoint whenever a
    # logical dataset is available, then carry its identity into the worker
    # request so a completed job materializes a ModelVersion.
    lineage_version = None
    lineage_algorithm = crud_algorithm.get_definition(db, "yolo_detection")
    if lineage_dataset_id and job_type == "yolo_detection" and lineage_algorithm is None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="YOLO 算法未注册，无法建立模型血缘，请联系管理员",
        )
    if lineage_dataset_id and job_type == "yolo_detection":
        frozen_sample_ids = [
            int(item["source_sample_id"])
            for item in source_sample_snapshot
            if isinstance(item, dict) and item.get("source_sample_id") is not None
        ]
        lineage_version = asyncio.run(
            dataset_service.create_version_from_tasks(
                db,
                int(lineage_dataset_id),
                dataset_service.CreateDatasetVersionFromTasksCommand(
                    source_task_ids=[int(value) for value in source_task_ids],
                    train_ratio=0.8,
                    val_ratio=0.1,
                    test_ratio=0.1,
                    sample_ids=frozen_sample_ids or None,
                    note="系统根据多任务训练候选快照自动生成训练版本",
                ),
                current_user,
                auto_freeze=True,
                selected_label_ids=[int(value) for value in cmd.label_ids],
                sample_conditions=cmd.sample_conditions,
                allowed_dataset_ids=lineage_dataset_ids,
            )
        )
        lineage_manifest = {
            "dataset_version_id": int(lineage_version.id),
            "dataset_id": int(lineage_version.dataset_id),
            "dataset_ids": [int(value) for value in lineage_dataset_ids],
            "source_dataset_ids": [int(value) for value in lineage_dataset_ids],
            "version_no": int(lineage_version.version_no),
            "manifest_rel_path": lineage_version.manifest_rel_path,
            "manifest_sha256": lineage_version.manifest_sha256,
            "label_set_signature": lineage_version.label_set_signature,
            "class_order_signature": lineage_version.class_order_signature,
            "dataset_label_contract_id": lineage_version.dataset_label_contract_id,
            "dataset_contract_revision": lineage_version.dataset_contract_revision,
            "labels_snapshot_json": lineage_version.labels_snapshot_json,
            "sample_count": int(lineage_version.sample_count or 0),
            "frozen_at": (
                lineage_version.frozen_at.isoformat()
                if lineage_version.frozen_at is not None
                else None
            ),
        }
        lineage_params = dict(effective_cmd.hyperparams or {})
        lineage_params.update(
            {
                "algorithm_key": lineage_algorithm.algorithm_key,
                "algorithm_id": int(lineage_algorithm.id),
                "dataset_version_id": int(lineage_version.id),
                "dataset_ids": lineage_dataset_ids,
                "dataset_manifest": lineage_manifest,
            }
        )
        effective_cmd = effective_cmd.model_copy(
            update={"hyperparams": lineage_params}
        )
    quality_policy_snapshot = {}
    if contract_mode:
        semantic_signature = label_set_signature(
            [int(row.id) for row in canonical_labels]
        )
        quality_policy_snapshot = quality_policy_service.resolve_policy_for_label_set(
            model_family=recipe.model_family,
            annotation_kind=recipe.annotation_kind,
            label_set_signature=semantic_signature,
        )
        if not quality_policy_snapshot:
            quality_policy_snapshot = {
                "policy_name": "__system_default__",
                "model_family": recipe.model_family,
                "annotation_kind": recipe.annotation_kind,
                "label_set_signature": semantic_signature,
                "source": "system_default",
            }
    contract_cmd = cmd
    if lineage_dataset_id is not None and cmd.dataset_id != lineage_dataset_id:
        contract_cmd = cmd.model_copy(update={"dataset_id": int(lineage_dataset_id)})
    selection_contract, selection_contract_digest, selection_contract_text = (
        _build_training_selection_contract(
            contract_cmd,
            mode=training_mode,
            source_task_ids=[int(value) for value in source_task_ids],
            selected_label_ids=(
                [int(row.id) for row in canonical_labels] if contract_mode else []
            ),
            class_names=(
                [str(row.label_value) for row in canonical_labels]
                if contract_mode
                else [str(item.get("class_name") or "") for item in merged_class_rows]
            ),
            dataset_version_id=(
                int(lineage_version.id) if lineage_version is not None else None
            ),
        )
    )
    # Resolve the family/branch only after the immutable DatasetVersion has
    # been created.  Its id is part of the canonical contract and prevents
    # two otherwise-identical selections from pointing at mutable data.
    parent_training_job_id = cmd.parent_training_job_id
    if parent_training_job_id is None and str(cmd.weights_source or "").lower() == "trained":
        # Legacy callers may submit a trained-weight reference before the
        # referenced row is available.  Preserve that storage token, but only
        # treat an existing job as a governed parent for lineage validation.
        if cmd.weights_ref_id and crud_yolo_train_job.get(db, int(cmd.weights_ref_id)) is not None:
            parent_training_job_id = cmd.weights_ref_id
    lineage = model_series_service.resolve_model_lineage(
        db,
        current_user=current_user,
        selection_contract=selection_contract,
        branch_key=cmd.branch_key,
        series_id=cmd.series_id,
        variant_id=cmd.variant_id,
        parent_model_version_id=cmd.parent_model_version_id,
        parent_training_job_id=parent_training_job_id,
        inheritance_mode=cmd.inheritance_mode,
        series_name=cmd.series_name,
        variant_name=cmd.variant_name,
        model_family=(
            cmd.model_family
            or str(getattr(recipe, "model_family", "") or "").strip()
            or str(cmd.training_recipe_key or "yolo_detection")
        ),
    )
    # ``begin_transaction`` below intentionally resets SQLAlchemy's autobegin
    # state.  Persist the catalog rows before entering that legacy transaction
    # or the created series/variant would be rolled back while their IDs remain
    # on the training task.
    db.commit()
    # Downstream persistence has many historical call sites.  Replace the
    # optional client lineage with the server-resolved immutable projection so
    # task, job and model-version rows cannot diverge.
    cmd = cmd.model_copy(
        update={
            "series_id": lineage.series_id,
            "variant_id": lineage.variant_id,
            "parent_model_version_id": lineage.parent_model_version_id,
            "parent_training_job_id": lineage.parent_training_job_id,
            "branch_key": lineage.branch_key,
            "inheritance_mode": lineage.inheritance_mode,
            "selection_contract": lineage.selection_contract,
        }
    )
    selection_contract_digest = lineage.selection_contract_sha256
    branch_identity_digest = lineage.branch_key
    selection_contract_text = lineage.selection_contract_json
    sample_conditions_text = json.dumps(
        cmd.sample_conditions or {}, ensure_ascii=False, separators=(",", ":")
    )
    replayed_job = None
    with begin_transaction(db):
        try:
            with db.begin_nested():
                training_task = create_training_task_row(
                    db,
                    owner_id=current_user.id,
                    name=normalized_name,
                    mode=training_mode,
                    status="PENDING",
                    primary_task_id=primary_task_id,
                    selected_presets_snapshot=preset_rows,
                    selected_label_items_snapshot=selected_label_rows,
                    merged_class_names_snapshot=merged_class_rows,
                    source_tasks_snapshot=[
                        item.model_dump() for item in preview.included_tasks
                    ],
                    ignored_tasks_snapshot=[
                        item.model_dump() for item in preview.ignored_tasks
                    ],
                    preview_summary=_preview_summary_dict(preview),
                    series_id=cmd.series_id,
                    variant_id=cmd.variant_id,
                    branch_key=branch_identity_digest,
                    selection_contract_sha256=selection_contract_digest,
                    selection_contract_json=selection_contract_text,
                    sample_conditions_json=sample_conditions_text,
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
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="Idempotency-Key 已用于其他训练请求",
                )
            existing_task = crud_training_task.get(
                db, training_task_id=int(replayed_job.training_task_id)
            ) if replayed_job.training_task_id else None
            if existing_task is None:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="幂等训练作业缺少关联训练任务，请联系管理员",
                )
            return _training_task_replay_response(existing_task, replayed_job)
        if contract_mode or is_plate_task_dimension:
            training_task.dataset_category_id = effective_dataset_category_id
            training_task.dataset_category_snapshot_json = json.dumps(
                (
                    {
                        "id": category_row.id,
                        "category_key": category_row.category_key,
                        "name": category_row.name,
                    }
                    if category_row
                    else None
                ),
                ensure_ascii=False,
            )
            training_task.training_recipe_key = cmd.training_recipe_key
            training_task.training_recipe_version = cmd.training_recipe_version
            training_task.runtime_profile = cmd.runtime_profile
            training_task.runtime_interface_version = cmd.runtime_interface_version
        if contract_mode:
            class_names = [str(row.label_value) for row in canonical_labels]
            selected_ids = [int(row.id) for row in canonical_labels]
            training_task.selected_label_ids_json = json.dumps(selected_ids)
            training_task.class_names_snapshot_json = json.dumps(
                class_names, ensure_ascii=False
            )
            training_task.label_set_signature = label_set_signature(selected_ids)
            training_task.class_order_signature = class_order_signature(class_names)
            training_task.source_sample_snapshot_json = json.dumps(
                source_sample_snapshot, ensure_ascii=False
            )
            training_task.quality_policy_snapshot_json = json.dumps(
                quality_policy_snapshot, ensure_ascii=False
            )
            db.add(training_task)
            db.flush()
        try:
            with db.begin_nested():
                job = crud_yolo_train_job.create(
                    db=db,
                    task_id=primary_task_id,
                    user_id=current_user.id,
                    model_weights=effective_cmd.model_weights,
                    epochs=effective_cmd.epochs,
                    imgsz=effective_cmd.imgsz,
                    category=normalized_name,
                    hyperparams_json=json.dumps(
                        effective_cmd.hyperparams or {}, ensure_ascii=False
                    ),
                    source_task_ids_json=json.dumps(source_task_ids, ensure_ascii=False),
                    execution_mode="local",
                    train_node_id=None,
                    training_task_id=training_task.id,
                    job_type=job_type,
                    component_status_json=(
                        json.dumps(component_status, ensure_ascii=False)
                        if component_status is not None
                        else None
                    ),
                    idempotency_key=retry_key,
                    idempotency_request_hash=retry_hash,
                    series_id=cmd.series_id,
                    variant_id=cmd.variant_id,
                    parent_model_version_id=cmd.parent_model_version_id,
                    parent_training_job_id=(
                        cmd.parent_training_job_id
                        or (
                            int(cmd.weights_ref_id)
                            if str(cmd.weights_source or "").lower() == "trained"
                            and cmd.weights_ref_id
                            else None
                        )
                        or (
                            int(cmd.iteration_source_job_id)
                            if cmd.iteration_source_job_id
                            else None
                        )
                    ),
                    branch_key=branch_identity_digest,
                    selection_contract_sha256=selection_contract_digest,
                    selection_contract_json=selection_contract_text,
                    sample_conditions_json=sample_conditions_text,
                    inheritance_mode=cmd.inheritance_mode,
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
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="Idempotency-Key 已用于其他训练请求",
                )
            # The task row was created in this transaction before the unique
            # job insert lost the race. Remove that unreferenced row and reuse
            # the winner's task/job pair.
            db.delete(training_task)
            db.flush()
            job = replayed_job
        if replayed_job is None and (contract_mode or is_plate_task_dimension):
            job.dataset_category_id = effective_dataset_category_id
            job.training_recipe_key = cmd.training_recipe_key
            job.training_recipe_version = cmd.training_recipe_version
            job.runtime_profile = cmd.runtime_profile
            job.runtime_interface_version = cmd.runtime_interface_version
        if replayed_job is None:
            # Keep lineage columns and the JSON contract in sync for both
            # label-first and task-first jobs.  Existing callers that omit
            # series/variant still receive a deterministic branch hash.
            job.series_id = cmd.series_id
            job.variant_id = cmd.variant_id
            job.parent_model_version_id = cmd.parent_model_version_id
            job.parent_training_job_id = (
                cmd.parent_training_job_id
                or (
                    int(cmd.weights_ref_id)
                    if str(cmd.weights_source or "").lower() == "trained"
                    and cmd.weights_ref_id
                    else None
                )
                or (int(cmd.iteration_source_job_id) if cmd.iteration_source_job_id else None)
            )
            job.branch_key = branch_identity_digest
            job.selection_contract_sha256 = selection_contract_digest
            job.selection_contract_json = selection_contract_text
            job.sample_conditions_json = sample_conditions_text
            job.inheritance_mode = cmd.inheritance_mode
            db.add(job)
            db.flush()
        if replayed_job is None and lineage_version is not None:
            job.dataset_manifest_json = json.dumps(
                lineage_params["dataset_manifest"], ensure_ascii=False
            )
            job.hyperparams_json = json.dumps(
                effective_cmd.hyperparams or {}, ensure_ascii=False
            )
        if replayed_job is None and contract_mode:
            job.class_names_json = training_task.class_names_snapshot_json
            job.label_set_signature = training_task.label_set_signature
            job.class_order_signature = training_task.class_order_signature
            job.source_sample_manifest_json = training_task.source_sample_snapshot_json
            job.quality_policy_snapshot_json = (
                training_task.quality_policy_snapshot_json
            )
            db.add(job)
            db.flush()
        if replayed_job is None:
            crud_training_task.update(
                db,
                training_task,
                latest_job_id=job.id,
                latest_job_status=job.status,
            )
    if replayed_job is not None:
        existing_task = crud_training_task.get(
            db, training_task_id=int(replayed_job.training_task_id)
        )
        if existing_task is None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="幂等训练作业缺少关联训练任务，请联系管理员",
            )
        return _training_task_replay_response(existing_task, replayed_job)
    if plate_recognition_train.is_plate_job_type(job_type):
        _enqueue_plate_recognition_train_job(job.id, primary_task_id, effective_cmd)
    else:
        _enqueue_label_dimension_train_job(job.id, primary_task_id, effective_cmd)
    return TrainingTaskCreateResponse(
        training_task_id=training_task.id,
        name=normalized_name,
        mode=training_mode,
        primary_task_id=primary_task_id,
        latest_job_id=job.id,
        series_id=training_task.series_id,
        variant_id=training_task.variant_id,
        branch_key=training_task.branch_key,
        selection_contract_sha256=training_task.selection_contract_sha256,
        included_task_count=preview.included_task_count,
        included_image_count=preview.included_image_count,
        ignored_task_count=preview.ignored_task_count,
        message=_message_for_create(
            preview, plate_training_target=plate_training_target
        ),
    )


def _task_name(db: Session, task_id: int | None) -> str | None:
    if not task_id:
        return None
    row = crud_task.get(db, task_id=task_id)
    return row.name if row else None


def build_training_task_detail_response(
    db: Session,
    row,
) -> TrainingTaskDetailResponse:
    selection_contract = crud_training_task.parse_snapshot(
        getattr(row, "selection_contract_json", None)
    )
    if not isinstance(selection_contract, dict):
        selection_contract = {}
    sample_conditions = crud_training_task.parse_snapshot(
        getattr(row, "sample_conditions_json", None)
    )
    if not isinstance(sample_conditions, dict):
        sample_conditions = crud_training_task.parse_snapshot(row.preview_summary_json).get(
            "sample_conditions", {}
        ) if isinstance(crud_training_task.parse_snapshot(row.preview_summary_json), dict) else {}
    return TrainingTaskDetailResponse(
        training_task_id=row.id,
        name=row.name,
        mode=row.mode,
        status=row.status,
        primary_task_id=row.primary_task_id,
        primary_task_name=_task_name(db, row.primary_task_id),
        selected_presets=[
            SelectedPresetSnapshotResponse(
                slug=item.get("slug", ""), name=item.get("name", "")
            )
            for item in crud_training_task.parse_snapshot(
                row.selected_presets_snapshot_json
            )
        ],
        selected_label_items=[
            SelectedLabelItemSnapshotResponse(
                preset_slug=item.get("preset_slug", ""),
                label_value=item.get("label_value", ""),
                display_name=item.get("display_name", ""),
                selected_order=int(item.get("selected_order", 0) or 0),
            )
            for item in crud_training_task.parse_snapshot(
                row.selected_label_items_snapshot_json
            )
        ],
        merged_classes=[
            MergedTrainingClassResponse(**item)
            for item in crud_training_task.parse_snapshot(
                row.merged_class_names_snapshot_json
            )
        ],
        source_tasks=[
            TrainingTaskIncludedTaskResponse(**item)
            for item in crud_training_task.parse_snapshot(
                row.source_tasks_snapshot_json
            )
        ],
        ignored_tasks=[
            TrainingTaskIgnoredTaskResponse(**item)
            for item in crud_training_task.parse_snapshot(
                row.ignored_tasks_snapshot_json
            )
        ],
        selected_label_ids=[
            int(value)
            for value in crud_training_task.parse_snapshot(row.selected_label_ids_json)
        ],
        label_set_signature=row.label_set_signature,
        class_order_signature=row.class_order_signature,
        dataset_category_id=row.dataset_category_id,
        training_recipe_key=row.training_recipe_key,
        training_recipe_version=row.training_recipe_version,
        runtime_profile=row.runtime_profile,
        runtime_interface_version=row.runtime_interface_version,
        sample_conditions=sample_conditions,
        latest_job_id=row.latest_job_id,
        latest_job_status=row.latest_job_status,
        series_id=getattr(row, "series_id", None),
        variant_id=getattr(row, "variant_id", None),
        branch_key=getattr(row, "branch_key", None),
        selection_contract_sha256=getattr(row, "selection_contract_sha256", None),
        selection_contract=selection_contract,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _training_task_source_task_ids(row) -> list[int]:
    task_ids: list[int] = []
    for item in crud_training_task.parse_snapshot(row.source_tasks_snapshot_json):
        try:
            task_id = int(item.get("task_id") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        if task_id > 0 and task_id not in task_ids:
            task_ids.append(task_id)
    primary_task_id = int(row.primary_task_id or 0)
    if not task_ids and primary_task_id > 0:
        task_ids.append(primary_task_id)
    return task_ids


def _user_can_access_training_task(
    db: Session,
    *,
    current_user: User,
    row,
) -> bool:
    if int(row.owner_id) == int(current_user.id) or is_task_superuser(current_user):
        return True

    source_task_ids = _training_task_source_task_ids(row)
    if not source_task_ids:
        return False
    visible_task_ids = {
        int(task_id)
        for (task_id,) in (
            crud_task.visible_query(db, owner_id=int(current_user.id))
            .with_entities(Task.id)
            .filter(Task.id.in_(source_task_ids))
            .all()
        )
    }
    return visible_task_ids == set(source_task_ids)


def get_training_task_detail(
    db: Session,
    *,
    current_user: User,
    training_task_id: int,
) -> TrainingTaskDetailResponse:
    row = crud_training_task.get(db, training_task_id)
    if not row or not _user_can_access_training_task(
        db,
        current_user=current_user,
        row=row,
    ):
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="训练任务不存在",
        )
    return build_training_task_detail_response(db, row)


def list_training_tasks(
    db: Session,
    *,
    current_user: User,
    page: int = 0,
    size: int = 20,
    name: str | None = None,
    search: str | None = None,
    status_value: str | None = None,
) -> tuple[list[TrainingTaskListRowResponse], int]:
    rows, total = crud_training_task.list_by_owner(
        db,
        current_user.id,
        page=page,
        size=size,
        name=name,
        search=search,
        status=status_value,
    )
    data = []
    for row in rows:
        source_tasks = crud_training_task.parse_snapshot(row.source_tasks_snapshot_json)
        source_task_count = len(source_tasks)
        primary_name = _task_name(db, row.primary_task_id)
        suffix = (
            f" + 等 {max(0, source_task_count - 1)} 个任务"
            if source_task_count > 1
            else ""
        )
        subtitle = None
        if row.primary_task_id:
            subtitle = f"{primary_name or '任务'} #{row.primary_task_id}{suffix}"
        data.append(
            TrainingTaskListRowResponse(
                training_task_id=row.id,
                name=row.name,
                mode=row.mode,
                status=row.status,
                primary_task_id=row.primary_task_id,
                primary_task_name=primary_name,
                source_task_count=source_task_count,
                display_subtitle=subtitle,
                latest_job_id=row.latest_job_id,
                latest_job_status=row.latest_job_status,
                series_id=getattr(row, "series_id", None),
                variant_id=getattr(row, "variant_id", None),
                branch_key=getattr(row, "branch_key", None),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return data, total


def _next_iteration_training_task_name(
    db: Session,
    *,
    owner_id: int,
    base_name: str,
) -> str:
    match = re.match(r"^(.*)-v(\d+)$", str(base_name or "").strip())
    root_name = match.group(1) if match else str(base_name or "").strip()
    suffix = int(match.group(2)) + 1 if match else 2
    while True:
        candidate = f"{root_name}-v{suffix}"
        if check_training_task_name_available(db, owner_id, candidate):
            return candidate
        suffix += 1


def iterate_training_task(
    db: Session,
    *,
    current_user: User,
    training_task_id: int,
) -> TrainingTaskCreateResponse:
    detail = get_training_task_detail(
        db, current_user=current_user, training_task_id=training_task_id
    )
    training_task = crud_training_task.get(db, training_task_id)
    candidate = _next_iteration_training_task_name(
        db,
        owner_id=current_user.id,
        base_name=detail.name,
    )
    selected_presets = [{"preset_slug": item.slug} for item in detail.selected_presets]
    selected_label_items = []
    raw_label_items = crud_training_task.parse_snapshot(
        training_task.selected_label_items_snapshot_json
    )
    for item in raw_label_items:
        selected_label_items.append(
            {
                "preset_slug": item.get("preset_slug", ""),
                "label_value": item.get("label_value", ""),
            }
        )
    source_job = (
        crud_yolo_train_job.get(db, int(detail.latest_job_id))
        if detail.latest_job_id
        else None
    )
    is_plate_iteration = detail.mode in {
        plate_recognition_train.PLATE_DETECTION_TRAINING_MODE,
        plate_recognition_train.PLATE_RECOGNITION_TRAINING_MODE,
    }
    plate_training_target = (
        str(getattr(source_job, "job_type", "") or "").strip()
        if is_plate_iteration and source_job
        else None
    )
    source_hyperparams: dict[str, Any] = {}
    if source_job and source_job.hyperparams_json:
        try:
            source_hyperparams = json.loads(source_job.hyperparams_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            source_hyperparams = {}
    source_hyperparams.pop(
        plate_recognition_train.PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM,
        None,
    )
    # Task-dimension iterations must preserve the original task scope.  Its
    # label ids are derived from the selected tasks, so using selected_label_ids
    # as the mode discriminator would silently convert it into label_dimension.
    task_dimension_mode = detail.mode == "task_dimension"
    contract_mode = bool(detail.selected_label_ids) and not task_dimension_mode
    cmd = CreateLabelDimensionTrainingTaskCommand(
        name=candidate,
        selected_presets=[] if contract_mode else selected_presets,
        selected_label_items=[] if contract_mode else selected_label_items,
        label_ids=list(detail.selected_label_ids) if contract_mode else [],
        dataset_category_id=(
            detail.dataset_category_id
            if (contract_mode or task_dimension_mode)
            else None
        ),
        training_recipe_key=(
            detail.training_recipe_key or "yolov11_detection"
            if (contract_mode or task_dimension_mode)
            else "yolov11_detection"
        ),
        training_recipe_version=(
            int(detail.training_recipe_version or 1)
            if (contract_mode or task_dimension_mode)
            else 1
        ),
        runtime_profile=(
            detail.runtime_profile or "yolov11_detection"
            if (contract_mode or task_dimension_mode)
            else "yolov11_detection"
        ),
        runtime_interface_version=(
            int(detail.runtime_interface_version or 1)
            if (contract_mode or task_dimension_mode)
            else 1
        ),
        sample_conditions=dict(detail.sample_conditions or {}),
        selected_task_ids=(
            [item.task_id for item in detail.source_tasks]
            if (is_plate_iteration or task_dimension_mode)
            else None
        ),
        model_weights=(
            plate_recognition_train.PLATE_POSE_PRETRAINED_MODEL
            if is_plate_iteration
            else str(getattr(source_job, "model_weights", None) or "yolo11n.pt")
        ),
        weights_source=(
            "preset"
            if is_plate_iteration
            or ((not contract_mode and not task_dimension_mode) or source_job is None)
            else "trained"
        ),
        weights_ref_id=(
            int(source_job.id)
            if (contract_mode or task_dimension_mode)
            and not is_plate_iteration
            and source_job is not None
            else None
        ),
        epochs=int(source_job.epochs or 50) if source_job else 50,
        imgsz=int(source_job.imgsz or 640) if source_job else 640,
        hyperparams=source_hyperparams,
        iteration_source_job_id=(
            int(source_job.id) if is_plate_iteration and source_job else None
        ),
        plate_training_target=plate_training_target,
    )
    return create_label_dimension_training_task(db, current_user=current_user, cmd=cmd)
