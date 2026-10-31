from __future__ import annotations

from datetime import datetime
import json
import math
import copy
from typing import Any, Mapping, Tuple

from fastapi import status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from labelu.internal.adapter.persistence import crud_dataset
from labelu.internal.application.command.dataset import (
    CreateDatasetCommand,
    CreateDatasetVersionCommand,
    CreateDatasetVersionFromTasksCommand,
    ReviewDatasetVersionCommand,
    UpdateDatasetCommand,
    UpdateDatasetVersionCommand,
)
from labelu.internal.application.response.dataset import (
    DatasetResponse,
    DatasetVersionResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser, user_has_task_access
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.dataset_label_contract import (
    DatasetLabelContract,
    DatasetLabelContractItem,
)
from labelu.internal.domain.models.annotation_prompt import AnnotationPromptBinding
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    DatasetCategory,
)
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import Task, TaskStatus
from labelu.internal.application.service.annotation_contract import (
    active_labels_by_ids,
    create_contract as create_task_annotation_contract,
    current_contract as current_task_annotation_contract,
    current_contracts_for_tasks,
    ensure_legacy_rect_contract,
    labels_snapshot,
    normalize_label_snapshot,
    contract_annotation_kind,
    materialize_dataset_contract_task_config,
)
from labelu.internal.common.label_contract import (
    annotation_sha256 as canonical_annotation_sha256,
    class_order_signature,
    label_set_signature,
)
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User
from labelu.internal.common.storage import get_storage_backend
from labelu.internal.common.task_annotation_completeness import audit_one_sample
from labelu.internal.common.training_sample_conditions import (
    normalize_sample_conditions,
    sample_matches_conditions,
    validate_sample_conditions,
)
from labelu.internal.application.service.yolo_dataset import _parse_sample_annotation
from labelu.internal.application.service.training_review_gate import (
    filter_trainable_samples,
)
import hashlib

_TRANSITIONS = {
    "INGESTING": {"INGESTING", "VALIDATING"},
    "VALIDATING": {"VALIDATING", "READY_TO_REVIEW"},
    # Freezing is deliberately handled only by freeze_version after an
    # explicit human approval; PATCH must not bypass that gate.
    "READY_TO_REVIEW": {"READY_TO_REVIEW"},
    "FROZEN": {"FROZEN", "RETIRED"},
    "RETIRED": {"RETIRED"},
}


def _asset_sha256_for_manifest(attachment: Any) -> str | None:
    stored = str(getattr(attachment, "sha256", None) or "").strip().lower()
    if stored:
        return stored
    path = str(getattr(attachment, "path", None) or "").strip()
    if not path or get_storage_backend().is_remote:
        return None
    local_path = get_storage_backend().get_local_path(path)
    if not local_path or not local_path.is_file():
        return None
    digest = hashlib.sha256()
    with local_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error(
    message: str,
    code=ErrorCode.CODE_30003_CLIENT_ERROR,
    http_status=status.HTTP_404_NOT_FOUND,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _dataset_response(
    row: Dataset,
    *,
    version_count: int = 0,
    db: Session | None = None,
) -> DatasetResponse:
    # Resolve the same effective contract used by search when a session is
    # available. Historical rows can have a null, stale, or cross-dataset
    # pointer; displaying that pointer would disagree with the search result.
    contract = (
        _dataset_contract_or_none(db, row)
        if db is not None
        else getattr(row, "label_contract", None)
    )
    contract_labels = []
    contract_label_ids: list[int] = []
    contract_revision = None
    contract_status = None
    contract_annotation_kind = None
    contract_label_set_signature = None
    contract_class_order_signature = None
    if contract is not None:
        contract_revision = int(contract.revision)
        contract_status = str(contract.status)
        contract_annotation_kind = str(contract.annotation_kind)
        contract_label_set_signature = str(contract.label_set_signature)
        contract_class_order_signature = str(contract.class_order_signature)
        try:
            contract_labels = json.loads(contract.labels_snapshot_json or "[]")
        except (TypeError, ValueError):
            contract_labels = []
        try:
            contract_label_ids = [
                int(value) for value in json.loads(contract.label_ids_json or "[]")
            ]
        except (TypeError, ValueError):
            contract_label_ids = []
    return DatasetResponse(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        description=row.description,
        dataset_category_id=row.dataset_category_id,
        dataset_category_name=(
            row.dataset_category.name if row.dataset_category else None
        ),
        status=row.status,
        created_by=row.created_by,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version_count=version_count,
        dataset_label_contract_id=(int(contract.id) if contract is not None else None),
        dataset_label_contract_revision=contract_revision,
        dataset_label_contract_status=contract_status,
        dataset_label_contract_annotation_kind=contract_annotation_kind,
        dataset_label_set_signature=contract_label_set_signature,
        dataset_class_order_signature=contract_class_order_signature,
        dataset_label_ids=contract_label_ids,
        dataset_labels=contract_labels if isinstance(contract_labels, list) else [],
    )


def _version_response(
    row: DatasetVersion, *, dataset_name: str | None = None
) -> DatasetVersionResponse:
    return DatasetVersionResponse(
        id=row.id,
        dataset_id=row.dataset_id,
        dataset_name=dataset_name,
        dataset_category_id=row.dataset_category_id,
        version_no=row.version_no,
        status=row.status,
        manifest_rel_path=row.manifest_rel_path,
        manifest_sha256=row.manifest_sha256,
        label_set_signature=row.label_set_signature,
        class_order_signature=row.class_order_signature,
        sample_count=row.sample_count,
        quality_status=row.quality_status,
        review_status=row.review_status,
        review_comment=row.review_comment,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        metadata_json=row.metadata_json,
        dataset_category_snapshot_json=row.dataset_category_snapshot_json,
        dataset_label_contract_id=getattr(row, "dataset_label_contract_id", None),
        dataset_contract_revision=getattr(row, "dataset_contract_revision", None),
        labels_snapshot_json=getattr(row, "labels_snapshot_json", None),
        created_by=row.created_by,
        frozen_by=row.frozen_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        frozen_at=row.frozen_at,
        retired_at=row.retired_at,
    )


def _metadata_text(value):
    if value is None or isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _error(
            "metadata_json 必须是有效 JSON",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        ) from exc


def _can_access_dataset(db: Session, dataset: Dataset, user: User) -> bool:
    if is_task_superuser(user) or int(dataset.owner_id) == int(user.id):
        return True
    return (
        db.query(Task.id)
        .outerjoin(TaskCollaborator, TaskCollaborator.task_id == Task.id)
        .filter(
            Task.dataset_id == int(dataset.id),
            Task.deleted_at.is_(None),
            or_(
                Task.created_by == int(user.id),
                TaskCollaborator.user_id == int(user.id),
                Task.source_type == "third_party",
            ),
        )
        .first()
        is not None
    )


def _can_manage_dataset(dataset: Dataset, user: User) -> bool:
    return is_task_superuser(user) or int(dataset.owner_id) == int(user.id)


def _dataset_or_raise(
    db: Session, dataset_id: int, user: User, *, manage: bool = False
) -> Dataset:
    dataset = crud_dataset.get_dataset(db, dataset_id)
    if dataset is None:
        raise _error("数据集不存在")
    allowed = (
        _can_manage_dataset(dataset, user)
        if manage
        else _can_access_dataset(db, dataset, user)
    )
    if not allowed:
        raise _error(
            "无权访问该数据集",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return dataset


async def create(
    db: Session, cmd: CreateDatasetCommand, current_user: User
) -> DatasetResponse:
    name = cmd.name.strip()
    if not name:
        raise _error(
            "数据集名称不能为空",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    category = None
    if cmd.dataset_category_id is not None:
        category = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.id == cmd.dataset_category_id,
                DatasetCategory.deleted_at.is_(None),
                DatasetCategory.status == "active",
            )
            .first()
        )
        if category is None:
            raise _error(
                "数据集分类不存在或已停用",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            )
    row = Dataset(
        owner_id=current_user.id,
        name=name,
        description=cmd.description,
        dataset_category_id=category.id if category else None,
        status="ACTIVE",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        crud_dataset.create_dataset(db, row)
        if cmd.label_ids:
            _create_dataset_contract(
                db,
                dataset=row,
                label_ids=cmd.label_ids,
                annotation_kind=cmd.annotation_kind,
                created_by=current_user.id,
            )
        db.refresh(row)
    return _dataset_response(row, db=db)


async def update(
    db: Session, dataset_id: int, cmd: UpdateDatasetCommand, current_user: User
) -> DatasetResponse:
    row = _dataset_or_raise(db, dataset_id, current_user, manage=True)
    values = cmd.dict(exclude_unset=True)
    if "name" in values:
        values["name"] = str(values["name"] or "").strip()
        if not values["name"]:
            raise _error(
                "数据集名称不能为空",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            )
    if "dataset_category_id" in values:
        category_id = values["dataset_category_id"]
        if category_id is None:
            values["dataset_category_id"] = None
        else:
            category = (
                db.query(DatasetCategory)
                .filter(
                    DatasetCategory.id == category_id,
                    DatasetCategory.deleted_at.is_(None),
                    DatasetCategory.status == "active",
                )
                .first()
            )
            if category is None:
                raise _error(
                    "数据集分类不存在或已停用",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_400_BAD_REQUEST,
                )
    with begin_transaction(db):
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_by = current_user.id
        db.flush()
        db.refresh(row)
    return _dataset_response(row, db=db)


async def list_by(
    db: Session,
    current_user: User,
    page: int,
    size: int,
    search: str | None = None,
) -> Tuple[list[DatasetResponse], int]:
    rows, total = crud_dataset.list_datasets(
        db,
        page,
        size,
        user_id=None if is_task_superuser(current_user) else int(current_user.id),
        search=search,
    )
    if not rows:
        return [], total
    # A global taxonomy edit may have happened while this dataset was not
    # open. Refresh the pointer before serializing the management list so the
    # list view and the contract endpoint expose the same vocabulary.
    with begin_transaction(db):
        for row in rows:
            ensure_current_dataset_contract(
                db, row, created_by=current_user.id
            )
            db.refresh(row)
    version_counts = dict(
        db.query(DatasetVersion.dataset_id, func.count(DatasetVersion.id))
        .filter(DatasetVersion.dataset_id.in_([row.id for row in rows]))
        .group_by(DatasetVersion.dataset_id)
        .all()
    )
    return [
        _dataset_response(
            row,
            version_count=int(version_counts.get(row.id, 0)),
            db=db,
        )
        for row in rows
    ], total


async def get(db: Session, dataset_id: int, current_user: User) -> DatasetResponse:
    dataset = _dataset_or_raise(db, dataset_id, current_user)
    with begin_transaction(db):
        ensure_current_dataset_contract(
            db, dataset, created_by=current_user.id
        )
        db.refresh(dataset)
    version_count = (
        db.query(func.count(DatasetVersion.id))
        .filter(DatasetVersion.dataset_id == dataset.id)
        .scalar()
    )
    return _dataset_response(
        dataset,
        version_count=int(version_count or 0),
        db=db,
    )


async def create_version(
    db: Session, dataset_id: int, cmd: CreateDatasetVersionCommand, current_user: User
) -> DatasetVersionResponse:
    dataset = _dataset_or_raise(db, dataset_id, current_user, manage=True)
    if crud_dataset.get_version_by_manifest_sha(db, cmd.manifest_sha256):
        raise _error(
            "清单摘要已存在",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    dataset_contract = _dataset_contract_or_none(db, dataset)
    row = DatasetVersion(
        dataset_id=dataset.id,
        dataset_category_id=dataset.dataset_category_id,
        version_no=crud_dataset.next_version_no(db, dataset.id),
        status="INGESTING",
        manifest_rel_path=cmd.manifest_rel_path,
        manifest_sha256=cmd.manifest_sha256,
        label_set_signature=cmd.label_set_signature,
        class_order_signature=cmd.class_order_signature,
        sample_count=cmd.sample_count,
        quality_status=cmd.quality_status,
        metadata_json=_metadata_text(cmd.metadata_json),
        dataset_category_snapshot_json=(
            json.dumps(
                {
                    "id": dataset.dataset_category.id,
                    "category_key": dataset.dataset_category.category_key,
                    "name": dataset.dataset_category.name,
                    "version": dataset.dataset_category.version,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if dataset.dataset_category
            else None
        ),
        dataset_label_contract_id=(
            int(dataset_contract.id) if dataset_contract is not None else None
        ),
        dataset_contract_revision=(
            int(dataset_contract.revision) if dataset_contract is not None else None
        ),
        labels_snapshot_json=(
            dataset_contract.labels_snapshot_json
            if dataset_contract is not None
            else None
        ),
        created_by=current_user.id,
    )
    with begin_transaction(db):
        crud_dataset.create_version(db, row)
    return _version_response(row)


def _dataset_contract_or_none(
    db: Session, dataset: Dataset
) -> DatasetLabelContract | None:
    # Keep list/search and response serialization on one effective-contract
    # rule.  Empty or malformed historical snapshots are skipped so a newer
    # usable contract can still be selected.
    return crud_dataset.effective_dataset_contract(db, dataset)


def _label_snapshot_semantic_rows(value: Any) -> list[dict[str, Any]] | None:
    """Return the label fields that define the current taxonomy semantics.

    Contract colors and the global taxonomy version are presentation/audit
    metadata.  A color-only edit must not create a new prompt vocabulary, but
    a label name, value, kind, attributes, or description change must create a
    new dataset contract revision.
    """

    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(value, list):
        return None
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        raw_id = item.get("label_id")
        try:
            label_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            label_id = None
        row = {
            "label_id": label_id,
            "label_key": str(item.get("label_key") or item.get("key") or "").strip(),
            "label_value": str(
                item.get("label_value") or item.get("value") or ""
            ).strip(),
            "annotation_kind": str(item.get("annotation_kind") or "").strip(),
            "attributes": (
                item.get("attributes") if isinstance(item.get("attributes"), list) else []
            ),
        }
        description = str(item.get("description") or "").strip()
        if description:
            row["description"] = description
        rows.append(row)
    return rows


def _dataset_contract_matches_labels(
    contract: DatasetLabelContract,
    *,
    label_ids: list[int],
    snapshot: list[dict[str, Any]],
    annotation_kind: str,
    label_set_signature_value: str,
    class_order_signature_value: str,
) -> bool:
    try:
        existing_ids = [
            int(value) for value in json.loads(contract.label_ids_json or "[]")
        ]
    except (TypeError, ValueError, json.JSONDecodeError):
        existing_ids = []
    expected_semantics = _label_snapshot_semantic_rows(snapshot)
    existing_semantics = _label_snapshot_semantic_rows(
        contract.labels_snapshot_json or "[]"
    )
    return (
        existing_ids == label_ids
        and str(contract.annotation_kind) == str(annotation_kind)
        and str(contract.label_set_signature) == str(label_set_signature_value)
        and str(contract.class_order_signature) == str(class_order_signature_value)
        and expected_semantics is not None
        and existing_semantics == expected_semantics
    )


def _stored_label_ids(value: Any) -> list[int]:
    try:
        raw = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            label_id = int(item)
        except (TypeError, ValueError):
            continue
        if label_id > 0 and label_id not in seen:
            seen.add(label_id)
            result.append(label_id)
    return result


def _sync_tasks_to_dataset_contract(
    db: Session,
    *,
    dataset: Dataset,
    previous: DatasetLabelContract,
    current: DatasetLabelContract,
    created_by: int | None,
) -> int:
    """Move task contracts to a newly materialized dataset revision.

    Task contracts remain immutable history. FULL tasks inherit the complete
    dataset label set; SUBSET tasks retain only their previously selected IDs.
    A task with completed annotations receives the normal ``review_required``
    status from ``create_contract`` instead of silently rewriting annotation
    history.
    """

    dataset_label_ids = _stored_label_ids(current.label_ids_json)
    if not dataset_label_ids:
        return 0
    dataset_label_id_set = set(dataset_label_ids)
    tasks = (
        db.query(Task)
        .filter(
            Task.deleted_at.is_(None),
            or_(
                Task.dataset_id == int(dataset.id),
                Task.dataset_label_contract_id == int(previous.id),
            ),
        )
        .all()
    )
    refreshed = 0
    for task in tasks:
        config_materialized = materialize_dataset_contract_task_config(
            task, current
        )
        if config_materialized:
            db.add(task)
        task_contract = current_task_annotation_contract(db, int(task.id))
        if task_contract is None:
            # A migration-era task may have a dataset pointer but no task
            # contract yet. FULL-scope tasks can inherit the immutable
            # dataset snapshot immediately; otherwise there is no safe way to
            # infer a subset without an existing task contract.
            scope = str(
                getattr(task, "task_label_scope_mode", None) or "FULL"
            ).upper()
            if scope == "FULL":
                try:
                    create_task_annotation_contract(
                        db,
                        task=task,
                        label_ids=dataset_label_ids,
                        created_by=created_by,
                        dataset_label_contract_id=int(current.id),
                        label_scope_mode=scope,
                    )
                except LabelUException:
                    # Keep the task gated when a legacy snapshot references a
                    # disabled/malformed label; the dataset revision itself
                    # remains valid and can be repaired explicitly.
                    continue
                task.dataset_label_contract_id = int(current.id)
                task.task_label_scope_mode = scope
                db.add(task)
            continue

        if (
            getattr(task, "dataset_label_contract_id", None) is not None
            and int(task.dataset_label_contract_id) == int(current.id)
            and getattr(task_contract, "dataset_label_contract_id", None) is not None
            and int(task_contract.dataset_label_contract_id) == int(current.id)
        ):
            continue

        scope = str(
            getattr(task_contract, "label_scope_mode", None)
            or getattr(task, "task_label_scope_mode", None)
            or "FULL"
        ).upper()
        if scope == "FULL":
            next_label_ids = list(dataset_label_ids)
        else:
            next_label_ids = [
                label_id
                for label_id in _stored_label_ids(task_contract.label_ids_json)
                if label_id in dataset_label_id_set
            ]
        if not next_label_ids:
            # Keep an invalid/malformed historical task closed rather than
            # manufacturing an unrelated full-label contract.
            continue

        previous_task_contract_id = int(task_contract.id)
        try:
            next_contract = create_task_annotation_contract(
                db,
                task=task,
                label_ids=next_label_ids,
                created_by=created_by,
                dataset_label_contract_id=int(current.id),
                label_scope_mode=scope,
            )
        except LabelUException:
            # A malformed legacy task must not prevent the dataset itself
            # from exposing the latest global vocabulary. It remains gated
            # until an operator repairs its task-level label contract.
            continue
        task.dataset_id = int(dataset.id)
        task.dataset_label_contract_id = int(current.id)
        task.task_label_scope_mode = scope
        db.add(task)
        if int(next_contract.id) != previous_task_contract_id:
            refreshed += 1
    db.flush()
    return refreshed


def ensure_current_dataset_contract(
    db: Session,
    dataset: Dataset,
    *,
    created_by: int | None = None,
) -> DatasetLabelContract | None:
    """Refresh a dataset contract when its global label definitions changed.

    Historical revisions remain immutable.  The current dataset pointer is
    advanced only when the active global rows no longer match the current
    contract snapshot, which also invalidates prompts through
    ``_create_dataset_contract``.
    """

    current = _dataset_contract_or_none(db, dataset)
    if current is None:
        return None
    try:
        label_ids = [
            int(value) for value in json.loads(current.label_ids_json or "[]")
        ]
    except (TypeError, ValueError, json.JSONDecodeError):
        return current
    if not label_ids:
        return current
    try:
        labels = active_labels_by_ids(db, label_ids)
    except LabelUException:
        # A disabled/deleted global label must not silently rewrite a
        # historical contract into an incomplete label set.
        return current
    snapshot = labels_snapshot(labels)
    values = [str(row.label_value) for row in labels]
    expected_label_set_signature = label_set_signature(label_ids)
    expected_class_order_signature = class_order_signature(values)
    if _dataset_contract_matches_labels(
        current,
        label_ids=label_ids,
        snapshot=snapshot,
        annotation_kind=str(current.annotation_kind or "bbox"),
        label_set_signature_value=expected_label_set_signature,
        class_order_signature_value=expected_class_order_signature,
    ):
        # A prior deployment may have materialized the dataset revision but
        # stopped before updating every task pointer. Repair those pointers
        # lazily on the next governed read without creating another dataset
        # revision.
        _sync_tasks_to_dataset_contract(
            db,
            dataset=dataset,
            previous=current,
            current=current,
            created_by=created_by,
        )
        return current
    refreshed = _create_dataset_contract(
        db,
        dataset=dataset,
        label_ids=label_ids,
        annotation_kind=str(current.annotation_kind or "bbox"),
        created_by=created_by,
    )
    _sync_tasks_to_dataset_contract(
        db,
        dataset=dataset,
        previous=current,
        current=refreshed,
        created_by=created_by,
    )
    return refreshed


def _normalized_int_list(value: Any) -> list[int]:
    """Parse a persisted/requested list of IDs without letting bad history abort reuse."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def _positive_int_or_none(value: Any) -> int | None:
    """Return a strictly positive integer ID, or ``None`` for malformed data."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or not text.isdecimal():
            return None
        try:
            parsed = int(text)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        return None
    return parsed if parsed > 0 else None


def _strict_positive_int_list(value: Any) -> tuple[list[int], bool]:
    """Parse an ID list and report whether every entry is well formed.

    ``_normalized_int_list`` intentionally tolerates legacy values for broad
    compatibility.  Historical snapshot evidence needs a stricter parser:
    silently dropping one malformed entry could make an incomplete snapshot
    look like a valid one.
    """

    if value is None:
        return [], True
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [], False
    if not isinstance(value, (list, tuple, set)):
        return [], False
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        parsed = _positive_int_or_none(item)
        if parsed is None or parsed in seen:
            return [], False
        seen.add(parsed)
        result.append(parsed)
    return result, True


def _strict_label_snapshot(
    value: Any,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Parse a label snapshot while distinguishing missing from malformed.

    The third return value indicates that the field was present.  An empty
    list is valid historical data but is not evidence of any selected labels;
    callers still decide whether that is sufficient to reuse a version.
    """

    if value is None:
        return [], True, False
    present = True
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [], False, present
    if not isinstance(value, list):
        return [], False, present
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            return [], False, present
        label_id = _positive_int_or_none(item.get("label_id"))
        if label_id is None or label_id in seen:
            return [], False, present
        seen.add(label_id)
        result.append(item)
    return result, True, present


def _strict_snapshot_label_ids(value: Any) -> tuple[list[int], bool, bool]:
    snapshot, valid, present = _strict_label_snapshot(value)
    if not valid:
        return [], False, present
    return [int(item["label_id"]) for item in snapshot], True, present


def _strict_manifest_sample_index(
    samples: Any,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Index manifest samples, rejecting malformed or duplicate sample IDs."""

    if not isinstance(samples, list):
        return {}, False
    indexed: dict[str, dict[str, Any]] = {}
    for item in samples:
        if not isinstance(item, dict):
            return {}, False
        sample_id = _positive_int_or_none(item.get("sample_id"))
        if sample_id is None or str(sample_id) in indexed:
            return {}, False
        indexed[str(sample_id)] = item
    return indexed, True


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _snapshot_label_ids(value: Any) -> list[int]:
    ids, valid, _present = _strict_snapshot_label_ids(value)
    return ids if valid else []


def _read_version_manifest(version: DatasetVersion) -> dict[str, Any]:
    """Read a historical manifest opportunistically for metadata compatibility.

    A missing/temporarily unavailable old object must only make that candidate
    ineligible for reuse; it must not prevent a new snapshot from being made.
    """

    key = str(getattr(version, "manifest_rel_path", None) or "").strip()
    if not key:
        return {}
    try:
        payload = json.loads(get_storage_backend().read_text(key))
    except Exception:  # storage backends may raise provider-specific errors
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_dataset_version_number_conflict(error: IntegrityError) -> bool:
    """Return whether an insert failed on the per-dataset version number.

    Constraint text is the only portable signal here: SQLite exposes the
    table/column names while MySQL usually exposes the named constraint or
    duplicate-key text.  Keep this narrowly scoped so unrelated integrity
    errors (for example a broken foreign key) are not converted into retries.
    """

    original = getattr(error, "orig", None)
    text = " ".join(
        str(value or "")
        for value in (error, original)
    ).lower()
    if "uq_dataset_version_number" in text:
        return True
    return (
        "dataset_version.dataset_id" in text
        and "dataset_version.version_no" in text
    ) or (
        "dataset_id" in text
        and "version_no" in text
        and "duplicate" in text
    )


def _is_dataset_version_manifest_conflict(error: IntegrityError) -> bool:
    """Return whether an insert failed on the immutable manifest digest."""

    original = getattr(error, "orig", None)
    text = " ".join(str(value or "") for value in (error, original)).lower()
    return (
        "uq_dataset_version_manifest_sha" in text
        or "dataset_version.manifest_sha256" in text
        or ("manifest_sha256" in text and "duplicate" in text)
    )


def _training_version_reuse_match(
    version: DatasetVersion,
    *,
    metadata: dict[str, Any],
    task_id: int,
    sample_ids: set[int],
    source_annotation_signatures: dict[str, str],
    source_annotation_canonical_signatures: dict[str, str],
    requested_label_ids: list[int],
    current_contract: DatasetLabelContract | None,
    expected_label_snapshot: list[dict[str, Any]],
    expected_label_snapshot_valid: bool = True,
    sample_conditions: Mapping[str, list[str]] | None = None,
) -> bool:
    """Check whether a frozen task-export version is the requested snapshot.

    New rows are matched from ``metadata_json``.  For historical rows whose
    metadata predates one of the fields, the immutable manifest is consulted as
    a compatibility fallback.  Every available contract identity/signature is
    still checked, so a changed dataset label contract cannot silently reuse a
    stale version.
    """

    if not isinstance(metadata, dict) or metadata.get("source") != "task_export":
        return False
    # A historical row without a current contract cannot be proven to use the
    # same label vocabulary.  Fail closed instead of allowing a request to
    # certify its own missing evidence below.
    if current_contract is None:
        return False
    if not expected_label_snapshot_valid:
        return False

    # A malformed version-level snapshot is not evidence that can be safely
    # repaired by the current request.  Reject the historical candidate and
    # let the caller create a fresh immutable version instead.
    version_snapshot, version_snapshot_valid, _version_snapshot_present = (
        _strict_label_snapshot(getattr(version, "labels_snapshot_json", None))
    )
    if not version_snapshot_valid:
        return False

    manifest: dict[str, Any] | None = None

    def ensure_manifest() -> dict[str, Any]:
        nonlocal manifest
        if manifest is None:
            manifest = _read_version_manifest(version)
        return manifest

    def candidate_list(key: str) -> tuple[list[int], bool]:
        if key in metadata:
            values, valid = _strict_positive_int_list(metadata.get(key))
            if not valid:
                return [], False
            # Empty source IDs are explicit evidence and must fail the set
            # comparison below.  An empty selected-label list is retained as
            # a legacy "not recorded" sentinel and may use fallback evidence.
            if values or key != "selected_label_ids":
                return values, True
        manifest_payload = ensure_manifest()
        if key in manifest_payload:
            values, valid = _strict_positive_int_list(manifest_payload.get(key))
            if not valid:
                return [], False
            if values or key != "selected_label_ids":
                return values, True
        if key == "source_sample_ids":
            manifest_samples, valid = _strict_manifest_sample_index(
                manifest_payload.get("samples")
            )
            if not valid:
                return [], False
            return [int(value) for value in manifest_samples], True
        return [], True

    source_task_ids, source_task_ids_valid = candidate_list("source_task_ids")
    source_sample_ids, source_sample_ids_valid = candidate_list("source_sample_ids")
    if not source_task_ids_valid or set(source_task_ids) != {int(task_id)}:
        return False
    if not source_sample_ids_valid or set(source_sample_ids) != sample_ids:
        return False

    # Global-tag filters are part of the immutable sample-selection identity.
    # Historical snapshots predate this field and remain reusable only for an
    # unfiltered request; a filtered request must create a fresh version whose
    # manifest records the exact conditions.
    expected_conditions = normalize_sample_conditions(sample_conditions)
    condition_marker = object()
    candidate_conditions: Any = metadata.get("sample_conditions", condition_marker)
    if candidate_conditions is condition_marker:
        manifest_payload_for_conditions = ensure_manifest()
        if "sample_conditions" in manifest_payload_for_conditions:
            candidate_conditions = manifest_payload_for_conditions.get(
                "sample_conditions"
            )
        else:
            candidate_conditions = {}
    if not isinstance(candidate_conditions, Mapping):
        return False
    if normalize_sample_conditions(candidate_conditions) != expected_conditions:
        return False

    # ``selected_label_ids`` was not present in the earliest task-export
    # metadata. Derive it from the immutable manifest/version snapshot. Never
    # use the request itself as evidence: a missing label record must be
    # treated as an ineligible historical candidate.
    selected_ids, selected_ids_valid = candidate_list("selected_label_ids")
    if not selected_ids_valid:
        return False
    needs_manifest_contract = not any(
        (
            getattr(version, "dataset_label_contract_id", None),
            getattr(version, "dataset_contract_revision", None),
            getattr(version, "label_set_signature", None),
            getattr(version, "class_order_signature", None),
            metadata.get("dataset_label_contract_id"),
            metadata.get("dataset_contract_revision"),
            metadata.get("label_set_signature"),
            metadata.get("class_order_signature"),
        )
    )
    manifest_payload = ensure_manifest() if (not selected_ids or needs_manifest_contract) else {}
    manifest_contract = _json_object(manifest_payload.get("annotation_contract"))
    manifest_labels, manifest_labels_valid, _manifest_labels_present = (
        _strict_label_snapshot(manifest_contract.get("labels"))
    )
    if not manifest_labels_valid:
        return False
    selected_ids_proven = bool(selected_ids)
    if not selected_ids:
        if "label_ids" in manifest_contract:
            selected_ids, label_ids_valid = _strict_positive_int_list(
                manifest_contract.get("label_ids")
            )
            if not label_ids_valid:
                return False
        selected_ids_proven = bool(selected_ids)
    if not selected_ids:
        selected_ids = [int(item["label_id"]) for item in version_snapshot]
        selected_ids_proven = bool(selected_ids)
    if not selected_ids:
        selected_ids = [int(item["label_id"]) for item in manifest_labels]
        selected_ids_proven = bool(selected_ids)

    # Contract identity may live in the row, metadata, or the manifest.  Old
    # rows can omit one or more fields, but any field that is present must
    # agree with the current contract.
    current_contract_id = (
        int(current_contract.id) if current_contract is not None else None
    )
    current_contract_revision = (
        int(current_contract.revision) if current_contract is not None else None
    )

    def observed_positive_values(values: tuple[Any, ...]) -> list[int] | None:
        observed: list[int] = []
        for value in values:
            if value is None:
                continue
            parsed = _positive_int_or_none(value)
            if parsed is None:
                return None
            observed.append(parsed)
        return observed

    observed_contract_ids = observed_positive_values(
        (
            getattr(version, "dataset_label_contract_id", None),
            metadata.get("dataset_label_contract_id"),
            manifest_contract.get("dataset_contract_id"),
        )
    )
    if observed_contract_ids is None:
        return False
    if current_contract_id is not None and any(
        value != current_contract_id for value in observed_contract_ids
    ):
        return False

    observed_revisions = observed_positive_values(
        (
            getattr(version, "dataset_contract_revision", None),
            metadata.get("dataset_contract_revision"),
            manifest_contract.get("dataset_contract_revision"),
        )
    )
    if observed_revisions is None:
        return False
    if current_contract_revision is not None and any(
        value != current_contract_revision for value in observed_revisions
    ):
        return False

    expected_label_signature = label_set_signature(requested_label_ids)
    expected_class_names = [
        str(item.get("label_value") or item.get("label_key") or "").strip()
        for item in expected_label_snapshot
        if isinstance(item, dict)
    ]
    expected_class_signature = class_order_signature(expected_class_names)
    observed_label_signatures = [
        str(value).strip()
        for value in (
            getattr(version, "label_set_signature", None),
            metadata.get("label_set_signature"),
            manifest_contract.get("label_set_signature"),
        )
        if str(value or "").strip()
    ]
    observed_class_signatures = [
        str(value).strip()
        for value in (
            getattr(version, "class_order_signature", None),
            metadata.get("class_order_signature"),
            manifest_contract.get("class_order_signature"),
        )
        if str(value or "").strip()
    ]
    if any(value != expected_label_signature for value in observed_label_signatures):
        return False
    if any(value != expected_class_signature for value in observed_class_signatures):
        return False

    if not selected_ids_proven:
        # A label-set signature is an immutable proof of the selected ID set
        # for old manifests that retained signatures but omitted the raw IDs.
        # Without it, accepting ``requested_label_ids`` would falsely reuse a
        # snapshot whose label contract is unknown.
        if expected_label_signature not in observed_label_signatures:
            return False
        selected_ids = list(requested_label_ids)
    if set(selected_ids) != set(requested_label_ids):
        return False

    # If an old row has no signatures at all, compare its frozen label snapshot
    # directly.  This is weaker than a revision identity but still prevents a
    # changed label name/order from being reused.
    if not observed_label_signatures or not observed_class_signatures:
        snapshot = version_snapshot or manifest_labels
        snapshot_ids = [int(item["label_id"]) for item in snapshot]
        snapshot_names = [
            str(item.get("label_value") or item.get("label_key") or "").strip()
            for item in snapshot
            if isinstance(item, dict)
        ]
        if snapshot and (
            set(snapshot_ids) != set(requested_label_ids)
            or class_order_signature(snapshot_names) != expected_class_signature
        ):
            return False

    # Raw row-data hashes were used by the current metadata writer, while
    # older metadata/manifests used canonical annotation hashes.  A historical
    # candidate is valid when every exact sample key carries either digest.
    def normalized_hash(value: Any) -> str:
        return str(value or "").strip().lower()

    expected_raw_hashes = {
        str(key): normalized_hash(value)
        for key, value in source_annotation_signatures.items()
    }
    expected_canonical_hashes = {
        str(key): normalized_hash(value)
        for key, value in source_annotation_canonical_signatures.items()
    }

    metadata_signatures = metadata.get("source_annotation_signatures")
    if isinstance(metadata_signatures, str):
        try:
            metadata_signatures = json.loads(metadata_signatures)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    use_metadata_signatures = (
        isinstance(metadata_signatures, dict) and bool(metadata_signatures)
    )
    if metadata_signatures is not None and not isinstance(metadata_signatures, dict):
        return False
    if use_metadata_signatures:
        normalized: dict[str, str] = {}
        for key, value in metadata_signatures.items():
            sample_id = _positive_int_or_none(key)
            digest_value = normalized_hash(value)
            if sample_id is None or not digest_value:
                return False
            normalized_key = str(sample_id)
            if str(key) != normalized_key:
                return False
            if normalized_key in normalized:
                return False
            normalized[normalized_key] = digest_value
        if set(normalized) != set(expected_raw_hashes):
            return False
        for sample_id, observed_hash in normalized.items():
            if observed_hash not in {
                expected_raw_hashes.get(sample_id),
                expected_canonical_hashes.get(sample_id),
            }:
                return False
    else:
        manifest_payload = ensure_manifest()
        manifest_by_id, manifest_samples_valid = _strict_manifest_sample_index(
            manifest_payload.get("samples")
        )
        if not manifest_samples_valid:
            return False
        for sample_id in sample_ids:
            item = manifest_by_id.get(str(sample_id))
            if not item:
                return False
            source_hash = normalized_hash(item.get("source_annotation_sha256"))
            canonical_hash = normalized_hash(item.get("annotation_sha256"))
            expected_raw = expected_raw_hashes.get(str(sample_id))
            expected_canonical = expected_canonical_hashes.get(str(sample_id))
            if source_hash:
                # Once a source hash is present it is authoritative; do not
                # silently fall back to a projected ``annotation_sha256``.
                if source_hash not in {expected_raw, expected_canonical}:
                    return False
            elif canonical_hash:
                # ``annotation_sha256`` may describe a label-subset projection
                # and is therefore only safe as the canonical full-source hash
                # when no source hash was recorded.
                if canonical_hash != expected_canonical:
                    return False
            else:
                return False
    return True


def _dataset_contract_payload(
    contract: DatasetLabelContract | None,
) -> dict[str, Any] | None:
    if contract is None:
        return None
    try:
        label_ids = [
            int(value) for value in json.loads(contract.label_ids_json or "[]")
        ]
    except (TypeError, ValueError):
        label_ids = []
    try:
        labels = json.loads(contract.labels_snapshot_json or "[]")
    except (TypeError, ValueError):
        labels = []
    return {
        "id": int(contract.id),
        "dataset_id": int(contract.dataset_id),
        "revision": int(contract.revision),
        "annotation_kind": str(contract.annotation_kind),
        "label_ids": label_ids,
        "labels": labels if isinstance(labels, list) else [],
        "label_set_signature": contract.label_set_signature,
        "class_order_signature": contract.class_order_signature,
        "status": str(contract.status),
        "created_by": contract.created_by,
        "frozen_by": contract.frozen_by,
        "created_at": contract.created_at,
        "frozen_at": contract.frozen_at,
    }


def _invalidate_annotation_prompts_for_contract(
    db: Session,
    *,
    dataset_id: int,
    contract_id: int,
    contract_revision: int,
    now: datetime | None = None,
) -> int:
    """Mark prompt artifacts from a replaced dataset contract as invalid.

    Prompt rows are immutable historical artifacts, so the row is retained and
    only its lifecycle status changes.  A dataset revision is unique per
    dataset; matching both the old contract id and dataset/revision also covers
    legacy rows that were created before ``dataset_label_contract_id`` was
    populated.
    """

    changed_at = now or datetime.now()
    return int(
        db.query(AnnotationPromptBinding)
        .filter(
            AnnotationPromptBinding.contract_revision == int(contract_revision),
            or_(
                AnnotationPromptBinding.dataset_label_contract_id == int(contract_id),
                AnnotationPromptBinding.dataset_id == int(dataset_id),
            ),
            AnnotationPromptBinding.status != "INVALIDATED",
        )
        .update(
            {"status": "INVALIDATED", "updated_at": changed_at},
            # Contract replacement can happen in a session that already
            # loaded prompt rows (for example, an admin workflow that
            # previews before saving). Keep the identity map consistent so a
            # subsequent history read cannot expose the old executable state.
            synchronize_session="fetch",
        )
        or 0
    )


def _create_dataset_contract(
    db: Session,
    *,
    dataset: Dataset,
    label_ids: list[int],
    annotation_kind: str,
    created_by: int,
    labels_snapshot_override: list[dict[str, Any]] | None = None,
    label_set_signature_override: str | None = None,
    class_order_signature_override: str | None = None,
    allow_missing_labels: bool = False,
) -> DatasetLabelContract:
    requested_ids = [int(value) for value in label_ids]
    try:
        labels = active_labels_by_ids(db, requested_ids)
    except LabelUException:
        if not allow_missing_labels or labels_snapshot_override is None:
            raise
        # Historical task contracts are immutable snapshots.  Some old
        # installations no longer have the corresponding global taxonomy
        # rows, so preserve the task IDs/snapshot for lineage and only create
        # membership rows for labels that still exist.
        labels = (
            db.query(AnnotationLabel)
            .filter(
                AnnotationLabel.id.in_(requested_ids),
                AnnotationLabel.deleted_at.is_(None),
                AnnotationLabel.status == "active",
            )
            .all()
        )
    ids = (
        requested_ids
        if labels_snapshot_override is not None
        else [int(row.id) for row in labels]
    )
    values = (
        [
            str(item.get("label_value") or item.get("label_key") or "")
            for item in labels_snapshot_override
            if isinstance(item, dict)
        ]
        if labels_snapshot_override is not None
        else [str(row.label_value) for row in labels]
    )
    snapshot = normalize_label_snapshot(
        labels_snapshot_override
        if labels_snapshot_override is not None
        else labels_snapshot(labels)
    )
    effective_annotation_kind = contract_annotation_kind(
        snapshot, fallback=annotation_kind
    )
    expected_label_set_signature = label_set_signature_override or label_set_signature(ids)
    expected_class_order_signature = (
        class_order_signature_override or class_order_signature(values)
    )
    existing = _dataset_contract_or_none(db, dataset)
    if existing is not None:
        if _dataset_contract_matches_labels(
            existing,
            label_ids=ids,
            snapshot=snapshot,
            annotation_kind=effective_annotation_kind,
            label_set_signature_value=expected_label_set_signature,
            class_order_signature_value=expected_class_order_signature,
        ):
            return existing
        # A new label contract revision changes the model vocabulary.  Keep
        # every old prompt row for audit/history, but make it impossible for
        # callers or operators to mistake it for an executable artifact.
        _invalidate_annotation_prompts_for_contract(
            db,
            dataset_id=int(dataset.id),
            contract_id=int(existing.id),
            contract_revision=int(existing.revision),
        )
        if str(existing.status).upper() == "FROZEN":
            existing.status = "SUPERSEDED"
    revision = (
        int(
            db.query(func.max(DatasetLabelContract.revision))
            .filter(DatasetLabelContract.dataset_id == int(dataset.id))
            .scalar()
            or 0
        )
        + 1
    )
    contract = DatasetLabelContract(
        dataset_id=int(dataset.id),
        revision=revision,
        annotation_kind=effective_annotation_kind,
        label_ids_json=json.dumps(ids, separators=(",", ":")),
        labels_snapshot_json=json.dumps(
            snapshot, ensure_ascii=False, separators=(",", ":")
        ),
        label_set_signature=expected_label_set_signature,
        class_order_signature=expected_class_order_signature,
        status="DRAFT",
        created_by=created_by,
        created_at=datetime.now(),
    )
    db.add(contract)
    db.flush()
    available_label_ids = {int(row.id) for row in labels}
    for order, label_id in enumerate(ids):
        if allow_missing_labels and int(label_id) not in available_label_ids:
            continue
        db.add(
            DatasetLabelContractItem(
                dataset_id=int(dataset.id),
                contract_id=int(contract.id),
                label_id=int(label_id),
                sort_order=order,
                status="ACTIVE",
                created_at=datetime.now(),
            )
        )
    dataset.dataset_label_contract_id = int(contract.id)
    db.add(dataset)
    db.flush()
    return contract


async def create_label_contract(
    db: Session,
    dataset_id: int,
    label_ids: list[int],
    annotation_kind: str,
    current_user: User,
):
    dataset = _dataset_or_raise(db, dataset_id, current_user, manage=True)
    with begin_transaction(db):
        contract = _create_dataset_contract(
            db,
            dataset=dataset,
            label_ids=label_ids,
            annotation_kind=annotation_kind,
            created_by=current_user.id,
        )
    from labelu.internal.application.response.dataset import (
        DatasetLabelContractResponse,
    )

    return DatasetLabelContractResponse(**_dataset_contract_payload(contract))


async def current_label_contract(db: Session, dataset_id: int, current_user: User):
    dataset = _dataset_or_raise(db, dataset_id, current_user)
    # A taxonomy administrator may have renamed a globally managed label after
    # this dataset was created.  Materialize the new immutable revision before
    # returning the contract so the editor and prompt compiler see one source.
    with begin_transaction(db):
        ensure_current_dataset_contract(
            db, dataset, created_by=current_user.id
        )
        db.refresh(dataset)
    from labelu.internal.application.response.dataset import (
        DatasetLabelContractResponse,
    )

    payload = _dataset_contract_payload(_dataset_contract_or_none(db, dataset))
    if payload is None:
        return None
    return DatasetLabelContractResponse(**payload)


async def list_label_contracts(db: Session, dataset_id: int, current_user: User):
    dataset = _dataset_or_raise(db, dataset_id, current_user)
    with begin_transaction(db):
        ensure_current_dataset_contract(
            db, dataset, created_by=current_user.id
        )
        db.refresh(dataset)
    rows = (
        db.query(DatasetLabelContract)
        .filter(DatasetLabelContract.dataset_id == int(dataset.id))
        .order_by(DatasetLabelContract.revision.desc())
        .all()
    )
    from labelu.internal.application.response.dataset import (
        DatasetLabelContractResponse,
    )

    return [
        DatasetLabelContractResponse(**_dataset_contract_payload(row)) for row in rows
    ]


async def freeze_label_contract(db: Session, contract_id: int, current_user: User):
    row = (
        db.query(DatasetLabelContract)
        .filter(DatasetLabelContract.id == int(contract_id))
        .one_or_none()
    )
    if row is None:
        raise _error("数据集标签契约不存在")
    dataset = _dataset_or_raise(db, int(row.dataset_id), current_user, manage=True)
    if str(row.status).upper() == "FROZEN":
        from labelu.internal.application.response.dataset import (
            DatasetLabelContractResponse,
        )

        return DatasetLabelContractResponse(**_dataset_contract_payload(row))
    with begin_transaction(db):
        row.status = "FROZEN"
        row.frozen_by = current_user.id
        row.frozen_at = datetime.now()
        dataset.dataset_label_contract_id = int(row.id)
        db.add(dataset)
        db.flush()
        db.refresh(row)
    from labelu.internal.application.response.dataset import (
        DatasetLabelContractResponse,
    )

    return DatasetLabelContractResponse(**_dataset_contract_payload(row))


async def freeze_label_contract_revision(
    db: Session, dataset_id: int, revision: int, current_user: User
):
    """Freeze a dataset contract using the stable dataset/revision identity.

    The revision-based endpoint is the public 7GAI contract.  The legacy
    contract-id helper remains available for old clients and migration tools.
    """
    dataset = _dataset_or_raise(db, dataset_id, current_user, manage=True)
    row = (
        db.query(DatasetLabelContract)
        .filter(
            DatasetLabelContract.dataset_id == int(dataset.id),
            DatasetLabelContract.revision == int(revision),
        )
        .one_or_none()
    )
    if row is None:
        raise _error("数据集标签契约修订不存在")
    return await freeze_label_contract(db, int(row.id), current_user)


async def create_version_from_tasks(
    db: Session,
    dataset_id: int,
    cmd: CreateDatasetVersionFromTasksCommand,
    current_user: User,
    *,
    auto_freeze: bool = False,
    metadata_extra: dict[str, Any] | None = None,
    selected_label_ids: list[int] | None = None,
    sample_conditions: Mapping[str, list[str]] | None = None,
    allowed_dataset_ids: list[int] | None = None,
) -> DatasetVersionResponse:
    """Generate a dataset manifest from completed annotation tasks.

    This is the business-facing entry point. Technical manifest fields are
    derived from the canonical task annotation contract and persisted as a
    reproducible manifest; the legacy manual registration endpoint remains
    available for imported external datasets.
    """
    dataset = _dataset_or_raise(
        db,
        dataset_id,
        current_user,
        # Task collaborators may trigger an automatic immutable snapshot, but
        # public governance endpoints remain owner/admin only.
        manage=not auto_freeze,
    )
    normalized_sample_conditions = validate_sample_conditions(db, sample_conditions)
    task_ids = sorted({int(value) for value in cmd.source_task_ids})
    if not task_ids or any(value <= 0 for value in task_ids):
        raise _error(
            "至少选择一个有效来源任务",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    ratio_total = float(cmd.train_ratio) + float(cmd.val_ratio) + float(cmd.test_ratio)
    if not math.isclose(ratio_total, 1.0, abs_tol=1e-6):
        raise _error(
            "训练、验证、测试比例之和必须为 1",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )

    tasks = (
        db.query(Task)
        .filter(Task.id.in_(task_ids), Task.deleted_at.is_(None))
        .order_by(Task.id.asc())
        .all()
    )
    if len(tasks) != len(task_ids):
        missing = sorted(set(task_ids) - {int(row.id) for row in tasks})
        raise _error(
            f"来源任务不存在或已删除: {missing}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    inaccessible_tasks = [
        int(row.id) for row in tasks if not user_has_task_access(current_user, row)
    ]
    if inaccessible_tasks:
        raise _error(
            f"无权访问来源任务: {inaccessible_tasks}",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    allowed_dataset_scope = {
        int(value)
        for value in (allowed_dataset_ids or [dataset.id])
        if int(value) > 0
    }
    allowed_dataset_scope.add(int(dataset.id))
    cross_dataset = [
        int(row.id)
        for row in tasks
        if getattr(row, "dataset_id", None) is not None
        and int(row.dataset_id) not in allowed_dataset_scope
    ]
    if cross_dataset:
        raise _error(
            f"来源任务不属于所选数据集范围: {cross_dataset}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    category_mismatch = [
        int(row.id)
        for row in tasks
        if dataset.dataset_category_id is not None
        and int(getattr(row, "dataset_category_id", 0) or 0)
        != int(dataset.dataset_category_id)
    ]
    if category_mismatch:
        raise _error(
            f"来源任务的数据集分类与当前数据集不一致: {category_mismatch}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    unfinished = [
        int(row.id)
        for row in tasks
        if str(row.status or "").upper() != TaskStatus.FINISHED.value
    ]
    # Governance-created versions still require a completed task.  The
    # internal training path uses ``auto_freeze`` and supplies an explicit
    # sample snapshot, so an INPROGRESS task may contribute the subset that
    # is already complete and quality-approved.
    if unfinished and not auto_freeze:
        raise _error(
            f"来源任务尚未完成标注: {unfinished}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    contracts = current_contracts_for_tasks(db, tasks)
    # Imported LabelU tasks may still carry a complete rectTool definition but
    # lack the immutable 7GAI contract created for newer tasks.  Backfill only
    # when every legacy label can be resolved; unresolved tasks remain blocked.
    for task in tasks:
        task_id = int(task.id)
        if task_id in contracts:
            continue
        with begin_transaction(db):
            contract = ensure_legacy_rect_contract(
                db, task=task, created_by=current_user.id
            )
        if contract is not None:
            contracts[task_id] = contract
    missing_contracts = sorted(set(task_ids) - set(contracts))
    if missing_contracts:
        raise _error(
            f"来源任务缺少有效标签契约: {missing_contracts}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    contract_rows = [contracts[task_id] for task_id in task_ids]
    dataset_contract = _dataset_contract_or_none(db, dataset)
    if dataset_contract is None:
        first_contract = contract_rows[0]
        try:
            first_ids = [
                int(value)
                for value in json.loads(first_contract.label_ids_json or "[]")
            ]
        except (TypeError, ValueError):
            first_ids = []
        if not first_ids:
            raise _error(
                "数据集标签契约为空，无法生成训练版本",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        try:
            first_snapshot = json.loads(first_contract.labels_snapshot_json or "[]")
        except (TypeError, ValueError):
            first_snapshot = []
        if not isinstance(first_snapshot, list):
            first_snapshot = []
        with begin_transaction(db):
            dataset_contract = _create_dataset_contract(
                db,
                dataset=dataset,
                label_ids=first_ids,
                annotation_kind=str(first_contract.annotation_kind or "bbox"),
                created_by=current_user.id,
                labels_snapshot_override=(first_snapshot if first_snapshot else None),
                label_set_signature_override=first_contract.label_set_signature,
                class_order_signature_override=first_contract.class_order_signature,
                # A legacy task contract is authoritative for this automatic
                # migration even when its global label rows were removed.
                allow_missing_labels=bool(first_snapshot),
            )
    try:
        dataset_label_ids = [
            int(value) for value in json.loads(dataset_contract.label_ids_json or "[]")
        ]
    except (TypeError, ValueError):
        dataset_label_ids = []
    try:
        labels_snapshot = json.loads(dataset_contract.labels_snapshot_json or "[]")
    except (TypeError, ValueError):
        labels_snapshot = []
    if not dataset_label_ids or not isinstance(labels_snapshot, list):
        raise _error(
            "数据集标签契约缺少有效标签",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    parsed_labels_snapshot, labels_snapshot_valid, _labels_snapshot_present = (
        _strict_label_snapshot(labels_snapshot)
    )
    selected_ids = list(
        dict.fromkeys(int(value) for value in (selected_label_ids or []))
    )
    if selected_ids and not set(selected_ids).issubset(set(dataset_label_ids)):
        raise _error(
            "训练标签必须来自数据集标签契约",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    effective_label_ids = selected_ids or list(dataset_label_ids)
    effective_labels_snapshot = (
        [
            item
            for item in parsed_labels_snapshot
            if int(item["label_id"]) in set(effective_label_ids)
        ]
        if labels_snapshot_valid
        else []
    )
    if len(effective_labels_snapshot) != len(effective_label_ids):
        raise _error(
            "训练标签快照不完整，无法生成可复现数据版本",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    selected_tokens = {
        str(token).strip()
        for item in effective_labels_snapshot
        for token in (item.get("label_value"), item.get("label_key"))
        if str(token or "").strip()
    }
    task_scope_modes: dict[int, str] = {}
    with begin_transaction(db):
        for task, task_contract in zip(tasks, contract_rows):
            try:
                task_label_ids = {
                    int(value)
                    for value in json.loads(task_contract.label_ids_json or "[]")
                }
            except (TypeError, ValueError):
                task_label_ids = set()
            if not task_label_ids or not task_label_ids.issubset(
                set(dataset_label_ids)
            ):
                raise _error(
                    f"任务 {task.id} 使用了数据集契约之外的标签",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            inferred_scope = (
                "FULL" if task_label_ids == set(dataset_label_ids) else "SUBSET"
            )
            if getattr(task, "dataset_id", None) is None:
                task.dataset_id = int(dataset.id)
            task.dataset_label_contract_id = int(dataset_contract.id)
            task.task_label_scope_mode = inferred_scope
            task_contract.dataset_label_contract_id = int(dataset_contract.id)
            task_contract.label_scope_mode = inferred_scope
            db.add(task)
            db.add(task_contract)
            task_scope_modes[int(task.id)] = inferred_scope
        db.flush()
    class_names = [
        str(item.get("label_value") or item.get("label_key") or "").strip()
        for item in effective_labels_snapshot
        if isinstance(item, dict)
    ]

    sample_query = (
        db.query(TaskSample)
        .options(joinedload(TaskSample.file))
        .filter(
            TaskSample.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
            TaskSample.state == SampleState.DONE.value,
        )
        .order_by(TaskSample.id.asc())
    )
    samples = sample_query.all()
    if cmd.sample_ids:
        requested_sample_ids = {int(value) for value in cmd.sample_ids}
        samples = [row for row in samples if int(row.id) in requested_sample_ids]
        found_sample_ids = {int(row.id) for row in samples}
        missing_samples = sorted(requested_sample_ids - found_sample_ids)
        if missing_samples:
            raise _error(
                f"所选样本不存在、未完成标注或不属于来源任务: {missing_samples[:20]}",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
    if normalized_sample_conditions:
        condition_filtered_samples = []
        for sample in samples:
            parsed = _parse_sample_annotation(sample)
            if parsed is None:
                continue
            _outer, result = parsed
            matched, _matched_values = sample_matches_conditions(
                result, normalized_sample_conditions
            )
            if matched:
                condition_filtered_samples.append(sample)
        samples = condition_filtered_samples
    if not samples:
        raise _error(
            (
                "所选全局标签条件未匹配到任何已完成且可用于训练的样本"
                if normalized_sample_conditions
                else "来源任务没有已完成且可用于训练的样本"
            ),
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    missing_assets = [int(row.id) for row in samples if not getattr(row, "file", None)]
    if missing_assets:
        raise _error(
            f"样本缺少媒体文件: {missing_assets[:20]}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    total = len(samples)
    train_count = int(total * float(cmd.train_ratio))
    val_count = int(total * float(cmd.val_ratio))
    split_counts = {
        "train": train_count,
        "val": val_count,
        "test": total - train_count - val_count,
    }
    train_end = train_count
    val_end = train_count + val_count
    task_by_id = {int(row.id): row for row in tasks}
    manifest_samples = []
    for index, sample in enumerate(samples):
        if index < train_end:
            split = "train"
        elif index < val_end:
            split = "val"
        else:
            split = "test"
        try:
            annotation = json.loads(sample.data or "null")
        except (TypeError, ValueError):
            annotation = sample.data
        source_annotation_sha = canonical_annotation_sha256(annotation)
        if selected_ids and isinstance(annotation, dict):
            # LabelU historical rows commonly store the actual annotation as
            # a JSON string under ``result``; newer rows may store it inline.
            # Freeze both shapes into the manifest without changing the source
            # sample or losing unrelated outer metadata.
            annotation = copy.deepcopy(annotation)
            result_payload = annotation.get("result")
            result_is_text = isinstance(result_payload, str)
            if result_is_text:
                try:
                    result_payload = json.loads(result_payload)
                except (TypeError, ValueError):
                    result_payload = None
            target = result_payload if isinstance(result_payload, dict) else annotation
            if isinstance(target, dict):
                # A label contract may bind different geometry kinds to
                # different drawing tools.  Filter every geometry result so a
                # label-subset manifest cannot retain an unselected polygon
                # (or another non-rect geometry) beside the selected box.
                for tool_name in (
                    "rectTool",
                    "polygonTool",
                    "pointTool",
                    "lineTool",
                    "cuboidTool",
                ):
                    tool_payload = target.get(tool_name)
                    if not isinstance(tool_payload, dict) or not isinstance(
                        tool_payload.get("result"), list
                    ):
                        continue
                    tool_payload["result"] = [
                        item
                        for item in tool_payload["result"]
                        if isinstance(item, dict)
                        and str(item.get("label") or "").strip() in selected_tokens
                    ]
            if result_is_text and isinstance(result_payload, dict):
                annotation["result"] = json.dumps(
                    result_payload, ensure_ascii=False, separators=(",", ":")
                )
        attachment = sample.file
        manifest_samples.append(
            {
                "sample_id": int(sample.id),
                "inner_id": (
                    int(sample.inner_id) if sample.inner_id is not None else None
                ),
                "task_id": int(sample.task_id),
                "task_name": str(task_by_id[int(sample.task_id)].name or ""),
                "split": split,
                "state": str(sample.state),
                "asset_path": getattr(attachment, "path", None),
                "asset_url": getattr(attachment, "url", None),
                "asset_sha256": _asset_sha256_for_manifest(attachment),
                "annotation_sha256": canonical_annotation_sha256(annotation),
                "source_annotation_sha256": source_annotation_sha,
                "annotation": annotation,
            }
        )

    manifest = {
        "format": "7gai.dataset_manifest.v1",
        "dataset_id": int(dataset.id),
        "dataset_ids": sorted(
            {
                int(getattr(task, "dataset_id", 0) or 0)
                for task in tasks
                if int(getattr(task, "dataset_id", 0) or 0) > 0
            }
            | allowed_dataset_scope
        ),
        "source_task_ids": task_ids,
        "source_dataset_ids": sorted(
            {
                int(getattr(task, "dataset_id", 0) or 0)
                for task in tasks
                if int(getattr(task, "dataset_id", 0) or 0) > 0
            }
        ),
        "source_tasks": [
            {
                "id": int(task.id),
                "name": str(task.name or ""),
                "media_type": str(task.media_type or ""),
                "status": str(task.status or ""),
            }
            for task in tasks
        ],
        "annotation_contract": {
            "dataset_contract_id": int(dataset_contract.id),
            "dataset_contract_revision": int(dataset_contract.revision),
            "annotation_kind": str(dataset_contract.annotation_kind),
            "label_set_signature": label_set_signature(effective_label_ids),
            "class_order_signature": class_order_signature(class_names),
            "class_names": class_names,
            "labels": effective_labels_snapshot,
        },
        "task_contracts": [
            {
                "task_id": int(task.id),
                "contract_id": int(contract.id),
                "revision": int(contract.revision),
                "scope_mode": task_scope_modes.get(int(task.id), "FULL"),
                "label_ids": [
                    int(value) for value in json.loads(contract.label_ids_json or "[]")
                ],
            }
            for task, contract in zip(tasks, contract_rows)
        ],
        "split": {
            "ratios": {
                "train": float(cmd.train_ratio),
                "val": float(cmd.val_ratio),
                "test": float(cmd.test_ratio),
            },
            "counts": split_counts,
        },
        "sample_count": total,
        "selected_label_ids": effective_label_ids,
        "sample_conditions": normalized_sample_conditions,
        "samples": manifest_samples,
    }
    content = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    existing_digest_version = crud_dataset.get_version_by_manifest_sha(db, digest)
    if existing_digest_version is not None:
        # Automatic task snapshots are idempotent.  The public governance
        # endpoint intentionally keeps its historical 409 duplicate contract.
        if auto_freeze and str(existing_digest_version.status).upper() == "FROZEN":
            return _version_response(existing_digest_version)
        raise _error(
            "相同来源和样本已经生成过数据版本",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    # The object must not be addressed by ``version_no``.  That number is
    # allocated with a max()+1 read and two concurrent requests can observe
    # the same value before either row is committed.  A digest-addressed key
    # makes each candidate immutable and prevents a losing request from
    # overwriting the winner's manifest while the database insert is retried.
    manifest_key = (
        f"datasets/dataset-{dataset.id}/auto-{digest}/manifest.json"
        if auto_freeze
        else f"datasets/dataset-{dataset.id}/v-{digest}/manifest.json"
    )
    storage = get_storage_backend()
    storage.save_bytes(content, manifest_key, content_type="application/json")
    metadata = {
        "source": "task_export",
        "source_task_ids": task_ids,
        "source_sample_ids": [int(row.id) for row in samples],
        "split_counts": split_counts,
        "split_ratios": {
            "train": float(cmd.train_ratio),
            "val": float(cmd.val_ratio),
            "test": float(cmd.test_ratio),
        },
        "dataset_label_contract_id": int(dataset_contract.id),
        "dataset_contract_revision": int(dataset_contract.revision),
        "class_names": class_names,
        "selected_label_ids": effective_label_ids,
        "selection_mode": "LABEL_SUBSET" if selected_ids else "FULL",
        "sample_conditions": normalized_sample_conditions,
        "generator": "7gai.task_export.v1",
        "note": cmd.note,
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    now = datetime.now()
    category_snapshot = (
        json.dumps(
            {
                "id": dataset.dataset_category.id,
                "category_key": dataset.dataset_category.category_key,
                "name": dataset.dataset_category.name,
                "version": dataset.dataset_category.version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if dataset.dataset_category
        else None
    )

    # ``uq_dataset_version_number`` is intentionally the allocator: it keeps
    # the schema unchanged and works on both SQLite and MySQL.  A max()+1 race
    # is resolved by starting a fresh transaction and taking the next number;
    # the digest-addressed manifest remains the same across attempts.
    max_attempts = 3
    for attempt in range(max_attempts):
        # The previous read/flush may have autobegun a transaction.  Explicitly
        # close it before starting the insert transaction so a failed flush
        # never leaves this Session in the PendingRollback state.
        db.rollback()
        existing_digest_version = crud_dataset.get_version_by_manifest_sha(
            db, digest
        )
        if existing_digest_version is not None:
            if auto_freeze and str(existing_digest_version.status).upper() == "FROZEN":
                return _version_response(existing_digest_version)
            raise _error(
                "相同来源和样本已经生成过数据版本",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )

        row: DatasetVersion | None = None
        try:
            with begin_transaction(db):
                version_no = crud_dataset.next_version_no(db, dataset.id)
                row = DatasetVersion(
                    dataset_id=dataset.id,
                    dataset_category_id=dataset.dataset_category_id,
                    version_no=version_no,
                    # Versions generated from a completed task have already
                    # passed the manifest and contract checks.  They can be
                    # frozen automatically; imported versions retain the
                    # explicit review flow.
                    status="FROZEN" if auto_freeze else "INGESTING",
                    manifest_rel_path=manifest_key,
                    manifest_sha256=digest,
                    label_set_signature=label_set_signature(effective_label_ids),
                    class_order_signature=class_order_signature(class_names),
                    dataset_label_contract_id=int(dataset_contract.id),
                    dataset_contract_revision=int(dataset_contract.revision),
                    labels_snapshot_json=json.dumps(
                        effective_labels_snapshot,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    sample_count=total,
                    quality_status="PASSED" if auto_freeze else "PENDING",
                    review_status="APPROVED" if auto_freeze else "PENDING",
                    review_comment=(
                        "系统根据已完成任务自动校验通过" if auto_freeze else None
                    ),
                    reviewed_by=current_user.id if auto_freeze else None,
                    reviewed_at=now if auto_freeze else None,
                    metadata_json=json.dumps(
                        metadata, ensure_ascii=False, separators=(",", ":")
                    ),
                    dataset_category_snapshot_json=category_snapshot,
                    created_by=current_user.id,
                    frozen_by=current_user.id if auto_freeze else None,
                    frozen_at=now if auto_freeze else None,
                )
                crud_dataset.create_version(db, row)
        except IntegrityError as exc:
            # SessionTransaction normally rolls back on context exit, but an
            # explicit rollback also covers mocked CRUD implementations and
            # makes the retry contract clear to SQLAlchemy 1.4/2.x callers.
            db.rollback()
            winner = crud_dataset.get_version_by_manifest_sha(db, digest)
            if winner is not None:
                if auto_freeze and str(winner.status).upper() == "FROZEN":
                    return _version_response(winner)
                raise _error(
                    "相同来源和样本已经生成过数据版本",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            if not (
                _is_dataset_version_number_conflict(exc)
                or _is_dataset_version_manifest_conflict(exc)
            ):
                raise
            if attempt + 1 >= max_attempts:
                raise
            # The next loop starts with a fresh max()+1 read.  Do not reuse the
            # failed ORM instance or delete the digest key: another request
            # cannot own this key unless the digest winner was found above.
            continue

        if row is None:  # defensive; create_version normally assigns it
            raise RuntimeError("数据集版本创建未返回记录")
        return _version_response(row)

    raise RuntimeError("数据集版本创建重试未返回结果")


async def ensure_training_version_for_task(
    db: Session,
    *,
    task: Task,
    current_user: User,
    selected_label_ids: list[int] | None = None,
    sample_conditions: Mapping[str, list[str]] | None = None,
) -> DatasetVersionResponse:
    """Return a frozen, reproducible snapshot for a task's trainable samples.

    This is the default training path for the 7GAI UI. The task must already
    point to a user-managed logical dataset. This function only creates or
    reuses the immutable version/manifest for this training run; it never
    creates a logical dataset or changes the task's dataset binding. A task
    does not have to be FINISHED: only samples that are DONE and pass the
    current annotation/quality gate are included.
    """
    if not user_has_task_access(current_user, task):
        raise _error(
            "无权使用该任务创建训练数据版本",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    normalized_sample_conditions = validate_sample_conditions(db, sample_conditions)
    # Training must use a dataset explicitly selected on the task.  Do not
    # silently create or attach a new "task training dataset" here: the
    # training snapshot is a version record, while the logical dataset is a
    # user-managed resource created from the task editor/data management page.
    dataset = None
    if getattr(task, "dataset_id", None):
        dataset = (
            db.query(Dataset)
            .filter(
                Dataset.id == int(task.dataset_id),
                Dataset.deleted_at.is_(None),
                Dataset.status == "ACTIVE",
            )
            .one_or_none()
        )
    if dataset is None:
        raise _error(
            "该任务未绑定数据集，请先在任务配置中选择数据集后再训练",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # Recalculate the sample-level admission set on every create request.
    # ``training_ready`` is intentionally not used here: it describes the
    # whole task and is therefore false for a partially annotated task even
    # when a useful subset is already available.
    all_samples = (
        db.query(TaskSample)
        .options(joinedload(TaskSample.file))
        .filter(
            TaskSample.task_id == int(task.id),
            TaskSample.deleted_at.is_(None),
        )
        .order_by(TaskSample.id.asc())
        .all()
    )
    done_samples = [
        row
        for row in all_samples
        if str(row.state or "").upper() == SampleState.DONE.value
    ]
    task_config = {}
    try:
        raw_config = json.loads(task.config or "{}")
        task_config = raw_config if isinstance(raw_config, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        task_config = {}

    # Imported legacy tasks may not carry the canonical UI config.  Preserve
    # their historical behavior (DONE + attachment is the available signal);
    # configured tasks receive the full per-sample annotation/geometry audit.
    has_quality_config = bool(task_config.get("tools") or task_config.get("tagList"))
    audited_samples = []
    excluded_reasons: dict[str, int] = {}
    for sample in done_samples:
        if has_quality_config:
            audit = audit_one_sample(sample, task_config)
            if not bool(audit.get("ok")):
                reason = (
                    "invalid_geometry"
                    if not bool(audit.get("geometry_ok"))
                    else "incomplete_annotation"
                )
                excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
                continue
        audited_samples.append(sample)

    trainable_samples = filter_trainable_samples(db, task=task, samples=audited_samples)
    trainable_samples = [
        sample
        for sample in trainable_samples
        if getattr(sample, "file", None) is not None
    ]
    missing_asset_count = len(audited_samples) - len(trainable_samples)
    if missing_asset_count:
        excluded_reasons["missing_media"] = missing_asset_count

    if normalized_sample_conditions:
        condition_candidates = trainable_samples
        trainable_samples = []
        for sample in condition_candidates:
            parsed = _parse_sample_annotation(sample)
            if parsed is None:
                continue
            _outer, result = parsed
            matched, _matched_values = sample_matches_conditions(
                result, normalized_sample_conditions
            )
            if matched:
                trainable_samples.append(sample)
        excluded_reasons["sample_conditions"] = (
            len(condition_candidates) - len(trainable_samples)
        )
    trainable_sample_ids = {int(row.id) for row in trainable_samples}
    if not trainable_sample_ids:
        raise _error(
            (
                "所选全局标签条件未匹配到任何已完成且质量合格的可训练样本"
                if normalized_sample_conditions
                else "任务没有已完成且质量合格的可训练样本"
            ),
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    source_annotation_signatures = {
        str(int(row.id)): hashlib.sha256(
            str(row.data or "").encode("utf-8")
        ).hexdigest()
        for row in trainable_samples
    }
    source_annotation_canonical_signatures = {}
    for row in trainable_samples:
        try:
            annotation_value = json.loads(row.data or "null")
        except (TypeError, ValueError, json.JSONDecodeError):
            annotation_value = row.data
        source_annotation_canonical_signatures[str(int(row.id))] = (
            canonical_annotation_sha256(annotation_value)
        )

    # Resolve the current dataset contract before looking for a reusable
    # version.  An omitted/empty selection is the UI's FULL sentinel and must
    # therefore compare against the contract's complete effective ID set.
    current_contract = _dataset_contract_or_none(db, dataset)
    current_contract_ids, current_contract_ids_valid = _strict_positive_int_list(
        getattr(current_contract, "label_ids_json", None)
        if current_contract is not None
        else None
    )
    selected_label_ids_for_creation = list(
        dict.fromkeys(int(value) for value in (selected_label_ids or []))
    )
    requested_label_ids = list(selected_label_ids_for_creation)
    if not requested_label_ids:
        requested_label_ids = list(current_contract_ids)
    current_contract_snapshot, current_snapshot_valid, _current_snapshot_present = (
        _strict_label_snapshot(
            getattr(current_contract, "labels_snapshot_json", None)
            if current_contract is not None
            else None
        )
    )
    requested_id_set = set(requested_label_ids)
    expected_label_snapshot_valid = current_contract_ids_valid and current_snapshot_valid
    expected_label_snapshot: list[dict[str, Any]] = []
    if expected_label_snapshot_valid:
        for item in current_contract_snapshot:
            label_id = _positive_int_or_none(item.get("label_id"))
            if label_id is None:
                expected_label_snapshot_valid = False
                break
            if label_id in requested_id_set:
                expected_label_snapshot.append(item)
        if not requested_id_set.issubset(
            {
                int(item["label_id"])
                for item in expected_label_snapshot
                if isinstance(item, dict)
            }
        ):
            expected_label_snapshot_valid = False

    # Reuse the latest automatically generated snapshot when the task's
    # admitted sample set has not changed.  This makes repeated clicks safe
    # while allowing new DONE samples to produce a new immutable version.
    versions = (
        db.query(DatasetVersion)
        .filter(
            DatasetVersion.dataset_id == int(dataset.id),
            DatasetVersion.status == "FROZEN",
        )
        .order_by(DatasetVersion.id.desc())
        .all()
    )
    for version in versions:
        try:
            metadata = json.loads(version.metadata_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        if _training_version_reuse_match(
            version,
            metadata=metadata,
            task_id=int(task.id),
            sample_ids=trainable_sample_ids,
            source_annotation_signatures=source_annotation_signatures,
            source_annotation_canonical_signatures=source_annotation_canonical_signatures,
            requested_label_ids=requested_label_ids,
            current_contract=current_contract,
            expected_label_snapshot=expected_label_snapshot,
            expected_label_snapshot_valid=expected_label_snapshot_valid,
            sample_conditions=normalized_sample_conditions,
        ):
            return _version_response(version)

    created = await create_version_from_tasks(
        db,
        int(dataset.id),
        CreateDatasetVersionFromTasksCommand(
            source_task_ids=[int(task.id)],
            train_ratio=0.8,
            val_ratio=0.1,
            test_ratio=0.1,
            sample_ids=sorted(trainable_sample_ids),
            note="系统根据当前已完成且质量合格样本自动生成训练快照",
        ),
        current_user,
        auto_freeze=True,
        metadata_extra={
            "inclusion_mode": "VALID_COMPLETED_SAMPLES",
            "source_task_status": str(task.status or ""),
            "source_annotation_signatures": source_annotation_signatures,
            "total_sample_count": len(all_samples),
            "completed_sample_count": len(done_samples),
            "trainable_sample_count": len(trainable_samples),
            "excluded_sample_count": len(all_samples) - len(trainable_samples),
            "completion_ratio": (
                len(done_samples) / len(all_samples) if all_samples else 0.0
            ),
            "quality_pass_ratio": (
                len(trainable_samples) / len(done_samples) if done_samples else 0.0
            ),
            "excluded_reasons": excluded_reasons,
            "sample_conditions": normalized_sample_conditions,
        },
        selected_label_ids=selected_label_ids_for_creation,
        sample_conditions=normalized_sample_conditions,
    )
    return created


def _version_or_raise(
    db: Session, version_id: int, current_user: User, *, manage: bool = False
) -> DatasetVersion:
    row = crud_dataset.get_version(db, version_id)
    if row is None:
        raise _error("数据集版本不存在")
    _dataset_or_raise(db, int(row.dataset_id), current_user, manage=manage)
    return row


async def list_versions(
    db: Session, dataset_id: int, current_user: User
) -> list[DatasetVersionResponse]:
    dataset = _dataset_or_raise(db, dataset_id, current_user)
    return [
        _version_response(row) for row in crud_dataset.list_versions(db, dataset.id)
    ]


async def list_all_versions(
    db: Session,
    current_user: User,
    page: int,
    size: int,
    search: str | None = None,
    active_learning_only: bool = False,
    dataset_id: int | None = None,
) -> Tuple[list[DatasetVersionResponse], int]:
    """List versions across all datasets visible to the current user."""

    rows, total = crud_dataset.list_dataset_versions(
        db,
        page,
        size,
        user_id=None if is_task_superuser(current_user) else int(current_user.id),
        dataset_id=dataset_id,
        search=search,
        active_learning_only=active_learning_only,
    )
    return [
        _version_response(version, dataset_name=dataset_name)
        for version, dataset_name in rows
    ], total


async def get_version_by_id(
    db: Session, version_id: int, current_user: User
) -> DatasetVersionResponse:
    """Return one dataset version through the dataset access contract."""
    return _version_response(_version_or_raise(db, version_id, current_user))


async def update_version(
    db: Session, version_id: int, cmd: UpdateDatasetVersionCommand, current_user: User
) -> DatasetVersionResponse:
    row = _version_or_raise(db, version_id, current_user, manage=True)
    values = cmd.model_dump(exclude_unset=True)
    target_status = values.pop("status", None)
    if row.status == "RETIRED":
        raise _error(
            "冻结或退役版本不可修改",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if row.status == "FROZEN" and (any(values) or target_status != "RETIRED"):
        raise _error(
            "冻结或退役版本不可修改",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if "metadata_json" in values:
        values["metadata_json"] = _metadata_text(values["metadata_json"])
    if target_status is not None and target_status not in _TRANSITIONS.get(
        row.status, set()
    ):
        raise _error(
            f"不允许从 {row.status} 迁移到 {target_status}",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    if target_status == "RETIRED" and row.status != "FROZEN":
        raise _error(
            "只有冻结版本可以退役",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    if target_status == "READY_TO_REVIEW":
        # Every new submission starts a fresh review decision. This also
        # clears a previous rejection/approval when a version is resubmitted.
        row.review_status = "PENDING"
        row.review_comment = None
        row.reviewed_by = None
        row.reviewed_at = None
    with begin_transaction(db):
        for key, value in values.items():
            setattr(row, key, value)
        if target_status:
            row.status = target_status
            if target_status == "RETIRED":
                row.retired_at = datetime.now()
        db.flush()
        db.refresh(row)
    return _version_response(row)


async def review_version(
    db: Session,
    version_id: int,
    cmd: ReviewDatasetVersionCommand,
    current_user: User,
) -> DatasetVersionResponse:
    """Approve or reject a version after technical validation.

    Rejection returns the version to VALIDATING so the owner can correct the
    evidence and submit it again. Approval keeps the version in
    READY_TO_REVIEW until the separate freeze action is confirmed.
    """
    row = _version_or_raise(db, version_id, current_user, manage=True)
    if row.status != "READY_TO_REVIEW":
        raise _error(
            "只有待评审版本可以执行评审",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    decision = str(cmd.decision).upper()
    comment = (cmd.comment or "").strip() or None
    if decision == "REJECT" and not comment:
        raise _error(
            "驳回评审必须填写原因",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    if decision == "APPROVE":
        validation = await validate_version(db, version_id, current_user)
        if not validation.valid:
            raise _error(
                "技术校验未通过，不能通过评审",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
    with begin_transaction(db):
        row.review_status = "APPROVED" if decision == "APPROVE" else "REJECTED"
        row.review_comment = comment
        row.reviewed_by = current_user.id
        row.reviewed_at = datetime.now()
        if decision == "REJECT":
            row.status = "VALIDATING"
        db.flush()
        db.refresh(row)
    return _version_response(row)


async def freeze_version(
    db: Session, version_id: int, current_user: User
) -> DatasetVersionResponse:
    row = _version_or_raise(db, version_id, current_user, manage=True)
    if row.status != "READY_TO_REVIEW":
        raise _error(
            "只有待评审版本可以冻结",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if row.review_status != "APPROVED":
        raise _error(
            "请先通过版本评审后再冻结",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    validation = await validate_version(db, version_id, current_user)
    if not validation.valid:
        raise _error(
            "数据集版本校验未通过",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    with begin_transaction(db):
        row.status = "FROZEN"
        row.frozen_by = current_user.id
        row.frozen_at = datetime.now()
        metadata = {}
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except (TypeError, ValueError):
            metadata = {}
        # Only active-learning datasets opt into automatic hand-off. Existing
        # datasets and the public freeze response remain unchanged.
        if (
            isinstance(metadata, dict)
            and metadata.get("source") == "active_learning_batch"
        ):
            from labelu.internal.application.service.workflow import (
                advance_expert_feedback_for_dataset,
            )

            advance_expert_feedback_for_dataset(
                db,
                dataset_version_id=int(row.id),
                user=current_user,
            )
        db.flush()
        db.refresh(row)
    return _version_response(row)


async def validate_version(db: Session, version_id: int, current_user: User):
    """Validate immutable manifest metadata before a version can be frozen."""
    from labelu.internal.application.response.dataset import (
        DatasetVersionValidationResponse,
    )

    row = _version_or_raise(db, version_id, current_user)
    checks = {
        "manifest_sha256_format": len(row.manifest_sha256 or "") == 64
        and all(char in "0123456789abcdefABCDEF" for char in row.manifest_sha256),
        "sample_count_non_negative": row.sample_count is not None
        and row.sample_count >= 0,
    }
    warnings: list[str] = []
    storage = get_storage_backend()
    local_path = (
        storage.get_local_path(row.manifest_rel_path) if not storage.is_remote else None
    )
    if local_path is not None:
        if local_path.is_file():
            digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
            checks["manifest_exists"] = True
            checks["manifest_sha256_matches"] = digest == row.manifest_sha256.lower()
        else:
            # Existing imported versions may reference an external manifest;
            # keep them freezable while exposing the missing local evidence.
            checks["manifest_exists"] = True
            checks["manifest_sha256_matches"] = True
            warnings.append("清单文件不在本地存储，未执行内容摘要校验")
    else:
        checks["manifest_exists"] = True
        checks["manifest_sha256_matches"] = True
        warnings.append("远端存储清单未下载，未执行内容摘要校验")
    return DatasetVersionValidationResponse(
        version_id=row.id, valid=all(checks.values()), checks=checks, warnings=warnings
    )
