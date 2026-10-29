from __future__ import annotations

import json
import hashlib
import colorsys
from datetime import datetime

from fastapi import status
from sqlalchemy import String, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.application.command.annotation_taxonomy import (
    CreateAnnotationLabelCommand,
    CreateDatasetCategoryCommand,
    CreateLabelAliasCommand,
    UpdateAnnotationLabelCommand,
    UpdateDatasetCategoryCommand,
    UpdateLabelAliasCommand,
    UpdateRuntimeTemplateCommand,
    UpsertRuntimeTemplateCommand,
    UpsertTrainingRecipeCommand,
)
from labelu.internal.application.response.annotation_taxonomy import (
    AnnotationLabelAliasResponse,
    AnnotationLabelResponse,
    DatasetCategoryResponse,
    RuntimePublishTemplateResponse,
    TrainingRecipeResponse,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_attributes import decode_label_attributes
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.system_dataset_category import (
    CONSISTENT_POOL_CATEGORY_KEY,
    is_system_dataset_category_key,
)
from labelu.internal.common.task_access import (
    is_consistent_pool_admin,
    is_task_superuser,
)
from labelu.internal.domain.models.annotation_contract import (
    TaskAnnotationContract,
    TaskAnnotationLabel,
)
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    AnnotationLabelAlias,
    DatasetCategory,
    LabelPresetLabel,
    RuntimePublishTemplate,
    TaxonomyAuditLog,
    TrainingRecipe,
)
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.dataset_label_contract import (
    DatasetLabelContractItem,
)
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.domain.models.user import User


def assert_taxonomy_admin(user: User) -> None:
    if not is_task_superuser(user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅系统管理员可以修改全局标签体系",
        )


def _conflict(message: str):
    raise LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_409_CONFLICT,
        message=message,
    )


def _audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    user_id: int | None,
    before=None,
    after=None,
    reason=None,
):
    db.add(
        TaxonomyAuditLog(
            entity_type=entity_type,
            entity_id=int(entity_id),
            action=action,
            before_json=(
                json.dumps(before, ensure_ascii=False, default=str)
                if before is not None
                else None
            ),
            after_json=(
                json.dumps(after, ensure_ascii=False, default=str)
                if after is not None
                else None
            ),
            change_reason=reason,
            changed_by=user_id,
            changed_at=datetime.now(),
        )
    )


def _label_refs(db: Session, label_id: int) -> int:
    """Return the number of distinct governance objects using a label.

    A label is a member of a contract, rather than a reference for every
    relationship row that happens to be present.  Count the owning object
    IDs explicitly so repeated/legacy relationship rows in one object cannot
    inflate the number.  Dataset and task annotation contracts are separate
    storage records, but a task annotation contract that points at a dataset
    label contract is the same logical contract for this statistic.  Presets
    remain part of the existing label-library reference contract and are
    counted once per preset as well.
    """
    preset_refs = (
        db.query(func.count(func.distinct(LabelPresetLabel.preset_id)))
        .filter(LabelPresetLabel.label_id == label_id)
        .scalar()
        or 0
    )
    dataset_contract_ids = {
        int(contract_id)
        for (contract_id,) in db.query(
            DatasetLabelContractItem.contract_id
        )
        .filter(DatasetLabelContractItem.label_id == label_id)
        .distinct()
        .all()
        if contract_id is not None
    }
    legacy_task_contract_ids: set[int] = set()
    for contract_id, dataset_contract_id in (
        db.query(
            TaskAnnotationContract.id,
            TaskAnnotationContract.dataset_label_contract_id,
        )
        .join(
            TaskAnnotationLabel,
            TaskAnnotationLabel.contract_id == TaskAnnotationContract.id,
        )
        .filter(TaskAnnotationLabel.label_id == label_id)
        .distinct()
        .all()
    ):
        if dataset_contract_id is not None:
            # Multiple task revisions can point at this same dataset contract;
            # the dataset contract is the single logical reference.
            dataset_contract_ids.add(int(dataset_contract_id))
        elif contract_id is not None:
            legacy_task_contract_ids.add(int(contract_id))
    return int(preset_refs) + len(dataset_contract_ids) + len(legacy_task_contract_ids)


def _label_resp(db: Session, row: AnnotationLabel) -> AnnotationLabelResponse:
    return AnnotationLabelResponse(
        id=row.id,
        label_key=row.label_key,
        label_value=row.label_value,
        annotation_kind=row.annotation_kind,
        color=row.color,
        attributes=decode_label_attributes(row.attributes_json),
        description=row.description,
        status=row.status,
        version=row.version,
        sort_order=row.sort_order,
        reference_count=_label_refs(db, int(row.id)),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_labels(
    db: Session,
    *,
    page: int = 0,
    size: int = 20,
    search: str | None = None,
    status_filter: str | None = None,
    annotation_kind: str | None = None,
    include_disabled: bool = True,
) -> tuple[list[AnnotationLabelResponse], int]:
    label_query = db.query(AnnotationLabel).filter(
        AnnotationLabel.deleted_at.is_(None)
    )
    if status_filter:
        label_query = label_query.filter(AnnotationLabel.status == status_filter)
    elif not include_disabled:
        label_query = label_query.filter(AnnotationLabel.status == "active")
    if annotation_kind:
        label_query = label_query.filter(
            AnnotationLabel.annotation_kind == annotation_kind
        )
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        label_query = label_query.filter(
            or_(
                AnnotationLabel.label_key.ilike(pattern, escape=LIKE_ESCAPE),
                AnnotationLabel.label_value.ilike(pattern, escape=LIKE_ESCAPE),
                AnnotationLabel.description.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = int(
        label_query.with_entities(func.count(AnnotationLabel.id)).scalar() or 0
    )
    rows = (
        label_query.order_by(AnnotationLabel.sort_order, AnnotationLabel.id)
        .offset(page * size)
        .limit(size)
        .all()
    )
    return [_label_resp(db, row) for row in rows], total


def _assert_unique_color(
    db: Session, color: str, *, exclude_label_id: int | None = None
) -> None:
    color_query = db.query(AnnotationLabel.id).filter(
        func.lower(AnnotationLabel.color) == color.lower()
    )
    if exclude_label_id is not None:
        color_query = color_query.filter(AnnotationLabel.id != exclude_label_id)
    if color_query.first():
        _conflict(f"color 已被其他标签使用: {color}")


def _next_available_label_color(db: Session) -> str:
    """Generate a readable color unique across active and disabled labels."""
    used = {
        str(value or "").strip().lower()
        for (value,) in db.query(AnnotationLabel.color).all()
        if str(value or "").strip()
    }
    for index in range(8192):
        hue = ((index * 137.508) % 360.0) / 360.0
        # colorsys uses the HLS order (hue, lightness, saturation). Keep this
        # sequence aligned with the frontend preview so the saved color matches
        # what the operator sees before submitting the form.
        red, green, blue = colorsys.hls_to_rgb(hue, 0.48, 0.68)
        candidate = "#{:02x}{:02x}{:02x}".format(
            round(red * 255), round(green * 255), round(blue * 255)
        )
        if candidate not in used:
            return candidate
    seed = 0
    while True:
        digest = hashlib.sha256(f"label-color:{seed}".encode()).hexdigest()
        candidate = f"#{digest[:6]}"
        if candidate not in used:
            return candidate
        seed += 1


def _attributes_json(attributes) -> str:
    payloads = []
    for item in attributes or []:
        payload = item.dict(exclude_none=True)
        if payload.get("type") == "string":
            payload.pop("options", None)
        payloads.append(payload)
    return json.dumps(
        payloads,
        ensure_ascii=False,
    )


def get_label(db: Session, label_id: int) -> AnnotationLabelResponse:
    row = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.id == label_id,
            AnnotationLabel.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="标签不存在",
        )
    return _label_resp(db, row)


def create_label(
    db: Session, cmd: CreateAnnotationLabelCommand, user: User
) -> AnnotationLabelResponse:
    assert_taxonomy_admin(user)
    normalized = cmd.label_value.strip().lower()
    if (
        db.query(AnnotationLabel.id)
        .filter(AnnotationLabel.normalized_label_value == normalized)
        .first()
    ):
        _conflict(f"label_value 已存在: {cmd.label_value}")
    color = cmd.color or _next_available_label_color(db)
    _assert_unique_color(db, color)
    row = AnnotationLabel(
        label_key=cmd.label_key.strip(),
        label_value=cmd.label_value.strip(),
        normalized_label_value=normalized,
        annotation_kind=cmd.annotation_kind,
        color=color,
        attributes_json=_attributes_json(cmd.attributes),
        description=cmd.description,
        status="active",
        version=1,
        sort_order=cmd.sort_order,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        entity_type="annotation_label",
        entity_id=row.id,
        action="create",
        user_id=user.id,
        after=_label_resp(db, row).dict(),
        reason=cmd.change_reason,
    )
    return _label_resp(db, row)


def update_label(
    db: Session, label_id: int, cmd: UpdateAnnotationLabelCommand, user: User
) -> AnnotationLabelResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.id == label_id, AnnotationLabel.deleted_at.is_(None))
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="标签不存在",
        )
    if int(row.version) != int(cmd.version):
        _conflict("标签已被其他管理员更新，请刷新后重试")
    before = _label_resp(db, row).dict()
    values = cmd.dict(exclude_unset=True, exclude={"version", "change_reason"})
    if "label_value" in values:
        next_label_value = str(values["label_value"]).strip()
        normalized_value = next_label_value.lower()
        value_query = db.query(AnnotationLabel.id).filter(
            AnnotationLabel.normalized_label_value == normalized_value,
            AnnotationLabel.id != int(row.id),
        )
        if value_query.first():
            _conflict(f"label_value 已被其他标签使用: {next_label_value}")
        values["label_value"] = next_label_value
        values["normalized_label_value"] = normalized_value
    color_changed = "color" in values and str(values["color"]).lower() != str(row.color).lower()
    if "color" in values:
        _assert_unique_color(db, values["color"], exclude_label_id=int(row.id))
    if "attributes" in values:
        values["attributes_json"] = _attributes_json(cmd.attributes)
        values.pop("attributes", None)
    for key, value in values.items():
        setattr(row, key, value)
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now()
    try:
        db.flush()
    except IntegrityError as exc:
        # The pre-check handles normal conflicts; this closes the race where
        # another transaction claims the same normalized value concurrently.
        db.rollback()
        if "normalized_label_value" in str(exc) or "uq_annotation_label_normalized_value" in str(exc):
            _conflict("label_value 已被其他标签同时使用，请刷新后重试")
        raise
    if color_changed:
        _sync_label_color_references(
            db,
            label_id=int(row.id),
            label_value=str(row.label_value),
            color=str(row.color),
        )
        db.flush()
    after = _label_resp(db, row)
    _audit(
        db,
        entity_type="annotation_label",
        entity_id=row.id,
        action="update",
        user_id=user.id,
        before=before,
        after=after.dict(),
        reason=cmd.change_reason,
    )
    return after


def _sync_label_color_references(
    db: Session, *, label_id: int, label_value: str, color: str
) -> tuple[int, int]:
    """Keep legacy task configs and versioned contract snapshots visually consistent.

    Color is presentation metadata, so signatures and label ordering remain unchanged.
    Historical contracts are updated as well because old task revisions can still be
    rendered by comparison and audit views.
    """
    normalized_value = str(label_value or "")
    task_count = 0
    for task in (
        db.query(Task)
        .filter(Task.deleted_at.is_(None), Task.config.isnot(None))
        .all()
    ):
        try:
            config = json.loads(task.config or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        changed = False
        for tool in config.get("tools") or []:
            if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
                continue
            tool_config = tool.get("config")
            if not isinstance(tool_config, dict):
                continue
            for attr in tool_config.get("attributes") or []:
                if not isinstance(attr, dict):
                    continue
                same_label = attr.get("labelId") == label_id
                if not same_label:
                    same_label = str(attr.get("value") or "") == normalized_value
                if same_label and attr.get("color") != color:
                    attr["color"] = color
                    changed = True
        if changed:
            task.config = json.dumps(config, ensure_ascii=False)
            task.updated_at = datetime.now()
            task_count += 1

    contract_count = 0
    for contract in db.query(TaskAnnotationContract).all():
        try:
            snapshot = json.loads(contract.labels_snapshot_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        changed = False
        for item in snapshot if isinstance(snapshot, list) else []:
            if not isinstance(item, dict):
                continue
            same_label = item.get("label_id") == label_id
            if not same_label:
                same_label = str(item.get("label_value") or "") == normalized_value
            if same_label and item.get("color") != color:
                item["color"] = color
                changed = True
        if changed:
            contract.labels_snapshot_json = json.dumps(snapshot, ensure_ascii=False)
            contract_count += 1
    return task_count, contract_count


def disable_label(
    db: Session, label_id: int, version: int, user: User
) -> AnnotationLabelResponse:
    return update_label(
        db,
        label_id,
        UpdateAnnotationLabelCommand(
            version=version, status="disabled", change_reason="soft delete"
        ),
        user,
    )


def _alias_resp(row: AnnotationLabelAlias) -> AnnotationLabelAliasResponse:
    return AnnotationLabelAliasResponse(
        id=row.id,
        source_system=row.source_system,
        external_label_value=row.external_label_value,
        label_id=row.label_id,
        label_value=row.label.label_value,
        status=row.status,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_aliases(db: Session) -> list[AnnotationLabelAliasResponse]:
    """Return the complete alias list for internal mutation/audit flows."""
    return [
        _alias_resp(row)
        for row in db.query(AnnotationLabelAlias)
        .filter(AnnotationLabelAlias.deleted_at.is_(None))
        .order_by(AnnotationLabelAlias.source_system, AnnotationLabelAlias.id)
        .all()
    ]


def list_aliases_page(
    db: Session,
    *,
    page: int = 0,
    size: int = 100,
    search: str | None = None,
) -> tuple[list[AnnotationLabelAliasResponse], int]:
    query = (
        db.query(AnnotationLabelAlias)
        .join(AnnotationLabel, AnnotationLabel.id == AnnotationLabelAlias.label_id)
        .filter(AnnotationLabelAlias.deleted_at.is_(None))
    )
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                cast(AnnotationLabelAlias.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(AnnotationLabelAlias.label_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(AnnotationLabelAlias.version, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                AnnotationLabelAlias.source_system.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                AnnotationLabelAlias.external_label_value.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                AnnotationLabelAlias.normalized_external_value.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                AnnotationLabelAlias.status.ilike(pattern, escape=LIKE_ESCAPE),
                AnnotationLabel.label_key.ilike(pattern, escape=LIKE_ESCAPE),
                AnnotationLabel.label_value.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = int(query.with_entities(func.count(AnnotationLabelAlias.id)).scalar() or 0)
    rows = (
        query.order_by(AnnotationLabelAlias.source_system, AnnotationLabelAlias.id)
        .offset(max(int(page), 0) * max(int(size), 1))
        .limit(max(int(size), 1))
        .all()
    )
    return [_alias_resp(row) for row in rows], total


def create_alias(
    db: Session, cmd: CreateLabelAliasCommand, user: User
) -> AnnotationLabelAliasResponse:
    assert_taxonomy_admin(user)
    label = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.id == cmd.label_id, AnnotationLabel.deleted_at.is_(None)
        )
        .first()
    )
    if not label:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="目标标签不存在",
        )
    row = AnnotationLabelAlias(
        source_system=cmd.source_system.strip().lower(),
        external_label_value=cmd.external_label_value.strip(),
        normalized_external_value=cmd.external_label_value.strip().lower(),
        label_id=cmd.label_id,
        status="active",
        version=1,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        _conflict("该来源系统的外部标签别名已存在")
    _audit(
        db,
        entity_type="annotation_label_alias",
        entity_id=row.id,
        action="create",
        user_id=user.id,
        after=_alias_resp(row).dict(),
    )
    return _alias_resp(row)


def update_alias(
    db: Session, alias_id: int, cmd: UpdateLabelAliasCommand, user: User
) -> AnnotationLabelAliasResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(AnnotationLabelAlias)
        .filter(
            AnnotationLabelAlias.id == alias_id,
            AnnotationLabelAlias.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="别名不存在",
        )
    if row.version != cmd.version:
        _conflict("别名已被更新")
    before = _alias_resp(row).dict()
    if cmd.label_id is not None:
        label = (
            db.query(AnnotationLabel)
            .filter(
                AnnotationLabel.id == cmd.label_id,
                AnnotationLabel.status == "active",
                AnnotationLabel.deleted_at.is_(None),
            )
            .first()
        )
        if not label:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=400,
                message="目标标签不存在或已停用",
            )
        row.label_id = cmd.label_id
    if cmd.status is not None:
        row.status = cmd.status
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now()
    db.flush()
    after = _alias_resp(row)
    _audit(
        db,
        entity_type="annotation_label_alias",
        entity_id=row.id,
        action="update",
        user_id=user.id,
        before=before,
        after=after.dict(),
    )
    return after


def disable_alias(
    db: Session, alias_id: int, version: int, user: User
) -> AnnotationLabelAliasResponse:
    return update_alias(
        db,
        alias_id,
        UpdateLabelAliasCommand(version=version, status="disabled"),
        user,
    )


def resolve_external_labels(
    db: Session, source_system: str, values: list[str]
) -> list[AnnotationLabel]:
    normalized = [str(value).strip().lower() for value in values]
    direct = {
        row.normalized_label_value: row
        for row in db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.normalized_label_value.in_(normalized),
            AnnotationLabel.status == "active",
            AnnotationLabel.deleted_at.is_(None),
        )
        .all()
    }
    aliases = {
        row.normalized_external_value: row.label
        for row in db.query(AnnotationLabelAlias)
        .filter(
            AnnotationLabelAlias.source_system
            == str(source_system or "").strip().lower(),
            AnnotationLabelAlias.normalized_external_value.in_(normalized),
            AnnotationLabelAlias.status == "active",
            AnnotationLabelAlias.deleted_at.is_(None),
        )
        .all()
    }
    result, missing = [], []
    for original, key in zip(values, normalized):
        label = aliases.get(key) or direct.get(key)
        if label is None:
            missing.append(original)
        else:
            result.append(label)
    if missing:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message=f"未知第三方标签: {missing}",
        )
    return result


def _category_resp(db: Session, row: DatasetCategory) -> DatasetCategoryResponse:
    refs = int(
        db.query(func.count(Task.id))
        .filter(Task.dataset_category_id == row.id, Task.deleted_at.is_(None))
        .scalar()
        or 0
    ) + int(
        db.query(func.count(YoloTrainJob.id))
        .filter(YoloTrainJob.dataset_category_id == row.id)
        .scalar()
        or 0
    ) + int(
        db.query(func.count(StrategyTaskIntegration.id))
        .filter(
            StrategyTaskIntegration.dataset_category_id == row.id,
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .scalar()
        or 0
    ) + int(
        db.query(func.count(TrainingTask.id))
        .filter(TrainingTask.dataset_category_id == row.id)
        .scalar()
        or 0
    ) + int(
        db.query(func.count(Dataset.id))
        .filter(Dataset.dataset_category_id == row.id, Dataset.deleted_at.is_(None))
        .scalar()
        or 0
    ) + int(
        db.query(func.count(DatasetVersion.id))
        .filter(DatasetVersion.dataset_category_id == row.id)
        .scalar()
        or 0
    )
    return DatasetCategoryResponse(
        id=row.id,
        category_key=row.category_key,
        name=row.name,
        description=row.description,
        sort_order=row.sort_order,
        status=row.status,
        version=row.version,
        reference_count=refs,
        is_system=is_system_dataset_category_key(row.category_key),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_categories(
    db: Session,
    *,
    page: int = 0,
    size: int = 20,
    search: str | None = None,
    status_filter: str | None = None,
    include_disabled: bool = True,
    current_user: User | None = None,
) -> tuple[list[DatasetCategoryResponse], int]:
    query = db.query(DatasetCategory).filter(DatasetCategory.deleted_at.is_(None))
    if current_user is not None and not is_consistent_pool_admin(current_user):
        query = query.filter(DatasetCategory.category_key != CONSISTENT_POOL_CATEGORY_KEY)
    if status_filter:
        query = query.filter(DatasetCategory.status == status_filter)
    elif not include_disabled:
        query = query.filter(DatasetCategory.status == "active")
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                DatasetCategory.category_key.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetCategory.name.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetCategory.description.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = int(query.with_entities(func.count(DatasetCategory.id)).scalar() or 0)
    rows = query.order_by(DatasetCategory.sort_order, DatasetCategory.id).offset(page * size).limit(size).all()
    return [
        _category_resp(db, row)
        for row in rows
    ], total


def resolve_dataset_category_key(
    db: Session,
    category_key: str | None,
    *,
    allow_disabled: bool = False,
) -> DatasetCategory | None:
    normalized = str(category_key or "").strip().lower()
    if not normalized:
        return None
    query = db.query(DatasetCategory).filter(
        func.lower(DatasetCategory.category_key) == normalized,
        DatasetCategory.deleted_at.is_(None),
    )
    if not allow_disabled:
        query = query.filter(DatasetCategory.status == "active")
    row = query.first()
    if row is None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message=f"未知或已停用的数据集分类: {category_key}",
        )
    return row


def create_category(
    db: Session, cmd: CreateDatasetCategoryCommand, user: User
) -> DatasetCategoryResponse:
    assert_taxonomy_admin(user)
    if is_system_dataset_category_key(cmd.category_key):
        _conflict("训练一致数据集是系统分类，不能手工创建")
    if (
        db.query(DatasetCategory.id)
        .filter(func.lower(DatasetCategory.category_key) == cmd.category_key.lower())
        .first()
    ):
        _conflict("category_key 已存在")
    row = DatasetCategory(
        category_key=cmd.category_key,
        name=cmd.name,
        description=cmd.description,
        sort_order=cmd.sort_order,
        status="active",
        version=1,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        entity_type="dataset_category",
        entity_id=row.id,
        action="create",
        user_id=user.id,
        after=_category_resp(db, row).dict(),
    )
    from labelu.internal.application.service import experiment_dataset_import

    experiment_dataset_import.wake_waiting_imports(
        db,
        status=experiment_dataset_import.IMPORT_STATUS_WAITING_FOR_CATEGORY,
        reason="dataset_category_created",
    )
    return _category_resp(db, row)


def update_category(
    db: Session, category_id: int, cmd: UpdateDatasetCategoryCommand, user: User
) -> DatasetCategoryResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(DatasetCategory)
        .filter(DatasetCategory.id == category_id, DatasetCategory.deleted_at.is_(None))
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="分类不存在",
        )
    if is_system_dataset_category_key(row.category_key):
        _conflict("训练一致数据集由系统自动维护，不能编辑或停用")
    if row.version != cmd.version:
        _conflict("分类已被更新")
    before = _category_resp(db, row).dict()
    for key, value in cmd.dict(exclude_unset=True, exclude={"version"}).items():
        setattr(row, key, value)
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now()
    db.flush()
    after = _category_resp(db, row)
    _audit(
        db,
        entity_type="dataset_category",
        entity_id=row.id,
        action="update",
        user_id=user.id,
        before=before,
        after=after.dict(),
    )
    from labelu.internal.application.service import experiment_dataset_import

    experiment_dataset_import.wake_waiting_imports(
        db,
        status=experiment_dataset_import.IMPORT_STATUS_WAITING_FOR_CATEGORY,
        reason="dataset_category_updated",
    )
    return after


def disable_category(
    db: Session, category_id: int, version: int, user: User
) -> DatasetCategoryResponse:
    return update_category(
        db,
        category_id,
        UpdateDatasetCategoryCommand(version=version, status="disabled"),
        user,
    )


def delete_category(
    db: Session, category_id: int, version: int, user: User
) -> DatasetCategoryResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(DatasetCategory)
        .filter(DatasetCategory.id == category_id, DatasetCategory.deleted_at.is_(None))
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="数据集分类不存在",
        )
    if is_system_dataset_category_key(row.category_key):
        _conflict("训练一致数据集由系统自动维护，不能删除")
    if int(row.version) != int(version):
        _conflict("分类已被更新")
    reference_count = _category_resp(db, row).reference_count
    if reference_count:
        _conflict(
            f"分类已被 {reference_count} 个任务/训练对象引用，不能删除；请先将引用对象改为其他分类或未分类"
        )
    before = _category_resp(db, row).dict()
    row.status = "disabled"
    row.deleted_at = datetime.now()
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now()
    db.flush()
    after = _category_resp(db, row)
    _audit(
        db,
        entity_type="dataset_category",
        entity_id=row.id,
        action="delete",
        user_id=user.id,
        before=before,
        after=after.dict(),
    )
    return after


def _recipe_resp(row: TrainingRecipe) -> TrainingRecipeResponse:
    return TrainingRecipeResponse(
        id=row.id,
        recipe_key=row.recipe_key,
        version=row.version,
        name=row.name,
        job_type=row.job_type,
        model_family=row.model_family,
        annotation_kind=row.annotation_kind,
        component_contract=json.loads(row.component_contract_json or "{}"),
        default_hyperparams=json.loads(row.default_hyperparams_json or "{}"),
        status=row.status,
    )


def list_recipes(db: Session) -> list[TrainingRecipeResponse]:
    """Return the complete recipe list for internal mutation/audit flows."""
    return [
        _recipe_resp(row)
        for row in db.query(TrainingRecipe)
        .filter(TrainingRecipe.deleted_at.is_(None))
        .order_by(TrainingRecipe.recipe_key, TrainingRecipe.version.desc())
        .all()
    ]


def list_recipes_page(
    db: Session,
    *,
    page: int = 0,
    size: int = 100,
    search: str | None = None,
) -> tuple[list[TrainingRecipeResponse], int]:
    query = db.query(TrainingRecipe).filter(TrainingRecipe.deleted_at.is_(None))
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                cast(TrainingRecipe.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(TrainingRecipe.version, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingRecipe.recipe_key.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingRecipe.name.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingRecipe.job_type.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingRecipe.model_family.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingRecipe.annotation_kind.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingRecipe.status.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = int(query.with_entities(func.count(TrainingRecipe.id)).scalar() or 0)
    rows = (
        query.order_by(TrainingRecipe.recipe_key, TrainingRecipe.version.desc())
        .offset(max(int(page), 0) * max(int(size), 1))
        .limit(max(int(size), 1))
        .all()
    )
    return [_recipe_resp(row) for row in rows], total


def create_recipe(
    db: Session, cmd: UpsertTrainingRecipeCommand, user: User
) -> TrainingRecipeResponse:
    assert_taxonomy_admin(user)
    row = TrainingRecipe(
        recipe_key=cmd.recipe_key,
        version=cmd.version,
        name=cmd.name,
        job_type=cmd.job_type,
        model_family=cmd.model_family,
        annotation_kind=cmd.annotation_kind,
        component_contract_json=json.dumps(cmd.component_contract, ensure_ascii=False),
        default_hyperparams_json=json.dumps(
            cmd.default_hyperparams, ensure_ascii=False
        ),
        status=cmd.status,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        _conflict("训练配方版本已存在；请创建新版本")
    response = TrainingRecipeResponse(
        id=row.id,
        recipe_key=row.recipe_key,
        version=row.version,
        name=row.name,
        job_type=row.job_type,
        model_family=row.model_family,
        annotation_kind=row.annotation_kind,
        component_contract=cmd.component_contract,
        default_hyperparams=cmd.default_hyperparams,
        status=row.status,
    )
    _audit(
        db,
        entity_type="training_recipe",
        entity_id=row.id,
        action="create_version",
        user_id=user.id,
        after=response.dict(),
    )
    return response


def disable_recipe(db: Session, recipe_id: int, user: User) -> TrainingRecipeResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(TrainingRecipe)
        .filter(TrainingRecipe.id == recipe_id, TrainingRecipe.deleted_at.is_(None))
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="训练配方不存在",
        )
    before = next(item for item in list_recipes(db) if item.id == row.id)
    row.status = "disabled"
    row.updated_by = user.id
    row.updated_at = datetime.now()
    db.flush()
    after = next(item for item in list_recipes(db) if item.id == row.id)
    _audit(
        db,
        entity_type="training_recipe",
        entity_id=row.id,
        action="disable",
        user_id=user.id,
        before=before.dict(),
        after=after.dict(),
    )
    return after


def list_runtime_templates(db: Session) -> list[RuntimePublishTemplateResponse]:
    """Return the complete runtime-template list for mutation/audit flows."""
    return [
        _runtime_template_response(row)
        for row in db.query(RuntimePublishTemplate)
        .filter(RuntimePublishTemplate.deleted_at.is_(None))
        .order_by(
            RuntimePublishTemplate.runtime_profile,
            RuntimePublishTemplate.interface_version.desc(),
        )
        .all()
    ]


def list_runtime_templates_page(
    db: Session,
    *,
    page: int = 0,
    size: int = 100,
    search: str | None = None,
) -> tuple[list[RuntimePublishTemplateResponse], int]:
    query = db.query(RuntimePublishTemplate).filter(
        RuntimePublishTemplate.deleted_at.is_(None)
    )
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                cast(RuntimePublishTemplate.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuntimePublishTemplate.interface_version, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuntimePublishTemplate.version, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuntimePublishTemplate.template_key.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuntimePublishTemplate.name.ilike(pattern, escape=LIKE_ESCAPE),
                RuntimePublishTemplate.environment_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuntimePublishTemplate.runtime_profile.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuntimePublishTemplate.aicm_model_set_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuntimePublishTemplate.expected_model_alg_type.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuntimePublishTemplate.status.ilike(pattern, escape=LIKE_ESCAPE),
                RuntimePublishTemplate.template_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    total = int(
        query.with_entities(func.count(RuntimePublishTemplate.id)).scalar() or 0
    )
    rows = (
        query.order_by(
            RuntimePublishTemplate.runtime_profile,
            RuntimePublishTemplate.interface_version.desc(),
        )
        .offset(max(int(page), 0) * max(int(size), 1))
        .limit(max(int(size), 1))
        .all()
    )
    return [_runtime_template_response(row) for row in rows], total


def _runtime_template_response(row: RuntimePublishTemplate) -> RuntimePublishTemplateResponse:
    return RuntimePublishTemplateResponse(
        id=row.id,
        template_key=row.template_key,
        name=row.name,
        environment_id=row.environment_id,
        runtime_profile=row.runtime_profile,
        interface_version=row.interface_version,
        aicm_model_set_id=row.aicm_model_set_id,
        expected_model_alg_type=row.expected_model_alg_type,
        capability_contract=json.loads(row.capability_contract_json or "{}"),
        template_sha256=row.template_sha256,
        is_default=bool(row.is_default),
        status=row.status,
        version=row.version,
    )


def create_runtime_template(
    db: Session, cmd: UpsertRuntimeTemplateCommand, user: User
) -> RuntimePublishTemplateResponse:
    assert_taxonomy_admin(user)
    if cmd.is_default:
        db.query(RuntimePublishTemplate).filter(
            RuntimePublishTemplate.environment_id == cmd.environment_id,
            RuntimePublishTemplate.runtime_profile == cmd.runtime_profile,
            RuntimePublishTemplate.is_default.is_(True),
        ).update({"is_default": False}, synchronize_session=False)
    row = RuntimePublishTemplate(
        template_key=cmd.template_key,
        name=cmd.name,
        environment_id=cmd.environment_id,
        runtime_profile=cmd.runtime_profile,
        interface_version=cmd.interface_version,
        aicm_model_set_id=cmd.aicm_model_set_id,
        expected_model_alg_type=cmd.expected_model_alg_type,
        capability_contract_json=json.dumps(
            cmd.capability_contract, ensure_ascii=False
        ),
        template_sha256=cmd.template_sha256,
        is_default=cmd.is_default,
        status=cmd.status,
        version=1,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        _conflict("运行时模板契约已存在")
    response = _runtime_template_response(row)
    _audit(
        db,
        entity_type="runtime_publish_template",
        entity_id=row.id,
        action="create",
        user_id=user.id,
        after=response.dict(),
    )
    return response


def update_runtime_template(
    db: Session,
    template_id: int,
    cmd: UpdateRuntimeTemplateCommand,
    user: User,
) -> RuntimePublishTemplateResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(RuntimePublishTemplate)
        .filter(
            RuntimePublishTemplate.id == template_id,
            RuntimePublishTemplate.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="运行时模板不存在",
        )
    if int(row.version) != int(cmd.version):
        _conflict("运行时模板已被其他管理员更新，请刷新后重试")

    before = _runtime_template_response(row)
    if cmd.is_default:
        db.query(RuntimePublishTemplate).filter(
            RuntimePublishTemplate.id != row.id,
            RuntimePublishTemplate.environment_id == cmd.environment_id,
            RuntimePublishTemplate.runtime_profile == cmd.runtime_profile,
            RuntimePublishTemplate.is_default.is_(True),
            RuntimePublishTemplate.deleted_at.is_(None),
        ).update({"is_default": False}, synchronize_session=False)

    row.template_key = cmd.template_key
    row.name = cmd.name
    row.environment_id = cmd.environment_id
    row.runtime_profile = cmd.runtime_profile
    row.interface_version = cmd.interface_version
    row.aicm_model_set_id = cmd.aicm_model_set_id
    row.expected_model_alg_type = cmd.expected_model_alg_type
    row.capability_contract_json = json.dumps(
        cmd.capability_contract, ensure_ascii=False
    )
    row.template_sha256 = cmd.template_sha256
    row.is_default = cmd.is_default
    row.status = cmd.status
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now()
    try:
        db.flush()
    except IntegrityError:
        _conflict("运行时模板契约已存在")

    after = _runtime_template_response(row)
    _audit(
        db,
        entity_type="runtime_publish_template",
        entity_id=row.id,
        action="update",
        user_id=user.id,
        before=before.dict(),
        after=after.dict(),
    )
    return after


def disable_runtime_template(
    db: Session, template_id: int, version: int, user: User
) -> RuntimePublishTemplateResponse:
    assert_taxonomy_admin(user)
    row = (
        db.query(RuntimePublishTemplate)
        .filter(
            RuntimePublishTemplate.id == template_id,
            RuntimePublishTemplate.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=404,
            message="运行时模板不存在",
        )
    if int(row.version) != int(version):
        _conflict("运行时模板已被其他管理员更新，请刷新后重试")
    before = next(item for item in list_runtime_templates(db) if item.id == row.id)
    row.status = "disabled"
    row.is_default = False
    row.version += 1
    row.updated_by = user.id
    row.updated_at = datetime.now()
    db.flush()
    after = next(item for item in list_runtime_templates(db) if item.id == row.id)
    _audit(
        db,
        entity_type="runtime_publish_template",
        entity_id=row.id,
        action="disable",
        user_id=user.id,
        before=before.dict(),
        after=after.dict(),
    )
    return after
