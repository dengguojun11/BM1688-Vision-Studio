from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any, Iterable

from fastapi import status
from sqlalchemy import func
from sqlalchemy.orm import Session

from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_contract import (
    annotation_sha256,
    class_order_signature,
    label_set_signature,
)
from labelu.internal.common.label_attributes import decode_label_attributes
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationScope,
    TaskAnnotationContract,
    TaskAnnotationLabel,
)
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import Task

CURRENT_CONTRACT_TRAINING_STATUSES = frozenset(
    {"draft", "active", "frozen", "review_required"}
)


def _snapshot_annotation_kind(value: Any, label_value: Any = None) -> str:
    """Normalize geometry kinds stored in a label-contract snapshot."""

    kind = str(value or "").strip().lower()
    # The canonical platePoint value is unambiguous. This also repairs old
    # snapshots written by the first taxonomy migration, which explicitly
    # recorded every legacy item as bbox.
    if str(label_value or "").strip().lower() == "platepoint":
        return "polygon"
    kind = kind or "bbox"
    if kind in {"rect", "rectangle"}:
        return "bbox"
    if kind in {"polygon", "bbox"}:
        return kind
    # Historical rows were all rectangle labels.  Keep malformed/unknown
    # values on the safe legacy path instead of dropping their label.
    return "bbox"


def normalize_label_snapshot(snapshot: Any) -> list[dict[str, Any]]:
    """Normalize per-label geometry before persisting a contract snapshot.

    ``annotation_kind`` was added after the first taxonomy seed.  In that
    window the canonical ``platePoint`` row could be stored as ``bbox`` even
    though the task configuration used ``polygonTool``.  The label value is
    the stable identity for that built-in label, so repair it while retaining
    every other snapshot field unchanged.
    """

    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = []
    if not isinstance(snapshot, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in snapshot:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        item["annotation_kind"] = _snapshot_annotation_kind(
            item.get("annotation_kind"), item.get("label_value")
        )
        normalized.append(item)
    return normalized


def _snapshot_tool_attribute(item: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "labelId": item.get("label_id"),
        "key": str(item.get("label_key") or item.get("label_value") or ""),
        "value": str(item.get("label_value") or item.get("label_key") or ""),
        "color": str(item.get("color") or "#1677ff"),
    }
    attributes = item.get("attributes")
    if isinstance(attributes, list) and attributes:
        payload["attributes"] = copy.deepcopy(attributes)
    return payload


def task_config_from_label_snapshot(
    snapshot: Any,
    *,
    base_config: Any = None,
) -> dict[str, Any]:
    """Materialize geometry tools from an immutable label snapshot.

    Dataset contracts intentionally keep one ordered list of labels while a
    task config has one attribute list per drawing tool.  This converter is
    the bridge between those representations.  Existing non-geometry tools
    are preserved; geometry attributes are replaced by the contract snapshot.
    """

    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = []
    if not isinstance(snapshot, list):
        snapshot = []

    rect_attributes: list[dict[str, Any]] = []
    polygon_attributes: list[dict[str, Any]] = []
    for raw in snapshot:
        if not isinstance(raw, dict):
            continue
        attribute = _snapshot_tool_attribute(raw)
        if not attribute["value"]:
            continue
        if (
            _snapshot_annotation_kind(
                raw.get("annotation_kind"), raw.get("label_value")
            )
            == "polygon"
        ):
            polygon_attributes.append(attribute)
        else:
            rect_attributes.append(attribute)

    config = copy.deepcopy(base_config) if isinstance(base_config, dict) else {}
    tools = config.get("tools")
    if not isinstance(tools, list):
        tools = []
        config["tools"] = tools

    def upsert_tool(name: str, tool_config: dict[str, Any]) -> None:
        existing = next(
            (
                item
                for item in tools
                if isinstance(item, dict) and item.get("tool") == name
            ),
            None,
        )
        if existing is None:
            tools.append({"tool": name, "config": tool_config})
            return
        previous = existing.get("config")
        merged = copy.deepcopy(previous) if isinstance(previous, dict) else {}
        merged.update(tool_config)
        existing["config"] = merged

    def remove_tool(name: str) -> None:
        """Drop a geometry tool that is absent from the selected snapshot.

        A task may already contain both geometry tools and then apply a
        dataset-label subset.  Keeping an old tool would make the saved task
        configuration claim labels that are not present in its immutable
        contract.  Non-geometry tools remain untouched.
        """

        tools[:] = [
            item
            for item in tools
            if not (isinstance(item, dict) and item.get("tool") == name)
        ]

    if rect_attributes:
        upsert_tool(
            "rectTool",
            {
                "isShowCursor": False,
                "showConfirm": False,
                "skipWhileNoDependencies": False,
                "drawOutsideTarget": False,
                "copyBackwardResult": False,
                "minWidth": 1,
                "minHeight": 1,
                "attributeConfigurable": True,
                "textConfigurable": True,
                "textCheckType": 4,
                "customFormat": "",
                "attributes": rect_attributes,
            },
        )
    else:
        remove_tool("rectTool")

    if polygon_attributes:
        is_plate_points = any(
            str(item.get("value") or "").strip().lower() == "platepoint"
            for item in polygon_attributes
        )
        upsert_tool(
            "polygonTool",
            {
                "isShowCursor": False,
                "lineType": 1,
                "drawOutsideTarget": False,
                "edgeAdsorption": True,
                "copyBackwardResult": False,
                "attributeConfigurable": True,
                "textConfigurable": True,
                "textCheckType": 0,
                "customFormat": "",
                "lowerLimitPointNum": 4 if is_plate_points else 3,
                "upperLimitPointNum": 4 if is_plate_points else 100,
                "attributes": polygon_attributes,
            },
        )
    else:
        remove_tool("polygonTool")

    # The dedicated plate worker is selected from the saved task config.  A
    # contract containing the canonical rectangle and four-corner labels is
    # therefore equivalent to applying the plate-detection preset, even when
    # the task was created directly from a dataset contract.
    rect_values = {
        str(item.get("value") or "").strip().lower()
        for item in rect_attributes
    }
    polygon_values = {
        str(item.get("value") or "").strip().lower()
        for item in polygon_attributes
    }
    if "doubleep" in rect_values and "platepoint" in polygon_values:
        config["defaultRectPresetId"] = "plateDetection"
    else:
        # A subset or non-plate contract must not inherit a stale plate
        # preset marker from the previous task configuration.
        config.pop("defaultRectPresetId", None)

    if rect_attributes or polygon_attributes:
        config.setdefault("commonAttributeConfigurable", True)
    return config


def contract_annotation_kind(snapshot: Any, *, fallback: str = "bbox") -> str:
    """Return the stable top-level kind for a per-label geometry snapshot.

    The legacy contract column is scalar.  A mixed rectangle/polygon
    contract therefore retains ``bbox`` as its adapter-compatible top-level
    value while the immutable label rows carry the authoritative per-label
    kinds.  Homogeneous polygon contracts can be represented directly.
    """

    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = []
    kinds = {
        _snapshot_annotation_kind(
            item.get("annotation_kind"), item.get("label_value")
        )
        for item in snapshot
        if isinstance(item, dict)
    }
    if kinds == {"polygon"}:
        return "polygon"
    if kinds == {"bbox"}:
        return "bbox"
    return str(fallback or "bbox").strip().lower() or "bbox"


def materialize_dataset_contract_task_config(
    task: Any,
    contract: Any,
    *,
    overwrite_empty_only: bool = True,
) -> bool:
    """Populate an unconfigured task from its dataset contract.

    Existing task configs, including historical composite plate configs, are
    left untouched.  The return value tells callers whether the task changed.
    """

    if contract is None:
        return False
    current = getattr(task, "config", None)
    if overwrite_empty_only and not task_config_is_unconfigured(current):
        return False
    generated = task_config_from_label_snapshot(
        getattr(contract, "labels_snapshot_json", None),
        base_config=_parse_config_object(current),
    )
    if not generated.get("tools"):
        return False
    task.config = json.dumps(generated, ensure_ascii=False)
    if str(getattr(task, "media_type", "") or "").upper() == "IMAGE":
        task.status = "CONFIGURED"
    return True


def _parse_config_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else None


def task_config_is_unconfigured(raw: Any) -> bool:
    """Whether a task config has no configured geometry tool yet."""

    parsed = _parse_config_object(raw)
    if parsed is None:
        return raw is None or not str(raw or "").strip()
    tools = parsed.get("tools")
    if not isinstance(tools, list):
        return True
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("tool") not in {
            "rectTool",
            "polygonTool",
            "pointTool",
            "lineTool",
            "cuboidTool",
        }:
            continue
        config = tool.get("config")
        if isinstance(config, dict) and isinstance(config.get("attributes"), list):
            if config.get("attributes"):
                return False
    return True


def active_labels_by_ids(
    db: Session, label_ids: Iterable[int], *, allow_disabled: bool = False
) -> list[AnnotationLabel]:
    requested = [int(value) for value in label_ids]
    if not requested or len(set(requested)) != len(requested):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="label_ids 不能为空且不能重复",
        )
    query = db.query(AnnotationLabel).filter(
        AnnotationLabel.id.in_(requested), AnnotationLabel.deleted_at.is_(None)
    )
    if not allow_disabled:
        query = query.filter(AnnotationLabel.status == "active")
    rows = {int(row.id): row for row in query.all()}
    missing = [value for value in requested if value not in rows]
    if missing:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"标签不存在或已停用: {missing}",
        )
    return [rows[value] for value in requested]


def labels_snapshot(labels: Iterable[AnnotationLabel]) -> list[dict[str, Any]]:
    snapshots = []
    for row in labels:
        item = {
            "label_id": int(row.id),
            "label_key": str(row.label_key),
            "label_value": str(row.label_value),
            "color": str(row.color),
            "annotation_kind": str(row.annotation_kind),
            "attributes": decode_label_attributes(row.attributes_json),
            "version": int(row.version or 1),
        }
        # Keep a semantic definition in the immutable contract snapshot when
        # one exists.  Omitting an empty field preserves equality with legacy
        # snapshots that predate prompt compilation and avoids manufacturing
        # a new contract revision on the next task update.
        description = str(row.description or "").strip()
        if description:
            item["description"] = description
        snapshots.append(item)
    return snapshots


def current_contract(db: Session, task_id: int) -> TaskAnnotationContract | None:
    task = db.query(Task).filter(Task.id == int(task_id)).first()
    if not task:
        return None
    if task.current_annotation_contract_id:
        row = (
            db.query(TaskAnnotationContract)
            .filter(TaskAnnotationContract.id == task.current_annotation_contract_id)
            .first()
        )
        if row:
            return row
    return (
        db.query(TaskAnnotationContract)
        .filter(
            TaskAnnotationContract.task_id == int(task_id),
            TaskAnnotationContract.status.in_(
                ["draft", "active", "frozen", "review_required"]
            ),
        )
        .order_by(TaskAnnotationContract.revision.desc())
        .first()
    )


def current_contracts_for_tasks(
    db: Session, tasks: Iterable[Task]
) -> dict[int, TaskAnnotationContract]:
    """Load current contracts for a task-list page without per-row queries."""
    task_rows = [task for task in tasks if getattr(task, "id", None)]
    if not task_rows:
        return {}

    current_ids = {
        int(task.current_annotation_contract_id)
        for task in task_rows
        if getattr(task, "current_annotation_contract_id", None)
    }
    current_rows = (
        db.query(TaskAnnotationContract)
        .filter(TaskAnnotationContract.id.in_(current_ids))
        .all()
        if current_ids
        else []
    )
    by_id = {int(row.id): row for row in current_rows}
    result = {
        int(task.id): by_id[int(task.current_annotation_contract_id)]
        for task in task_rows
        if getattr(task, "current_annotation_contract_id", None)
        and int(task.current_annotation_contract_id) in by_id
    }

    missing_task_ids = [
        int(task.id) for task in task_rows if int(task.id) not in result
    ]
    if not missing_task_ids:
        return result
    fallback_rows = (
        db.query(TaskAnnotationContract)
        .filter(
            TaskAnnotationContract.task_id.in_(missing_task_ids),
            TaskAnnotationContract.status.in_(CURRENT_CONTRACT_TRAINING_STATUSES),
        )
        .order_by(
            TaskAnnotationContract.task_id,
            TaskAnnotationContract.revision.desc(),
        )
        .all()
    )
    for row in fallback_rows:
        result.setdefault(int(row.task_id), row)
    return result


def ensure_legacy_rect_contract(
    db: Session, *, task: Task, created_by: int | None
) -> TaskAnnotationContract | None:
    """Create a contract for an imported task when its legacy rectTool is resolvable.

    Older tasks stored label definitions only in ``task.config``.  The 7GAI
    dataset-version flow needs the immutable taxonomy snapshot, so migrate the
    legacy definitions lazily and idempotently at the first governed export.
    If any configured label cannot be resolved, migration is deliberately
    skipped so callers keep the training gate closed instead of creating an
    incomplete label set.
    """
    existing = current_contract(db, int(task.id))
    if existing:
        return existing
    try:
        payload = json.loads(task.config or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    attributes: list[dict[str, Any]] = []
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        tool_name = str(tool.get("tool") or "").strip()
        if tool_name not in {"rectTool", "polygonTool"}:
            continue
        config = tool.get("config") if isinstance(tool.get("config"), dict) else {}
        raw_attributes = config.get("attributes")
        if isinstance(raw_attributes, list):
            attributes.extend(item for item in raw_attributes if isinstance(item, dict))
    if not attributes:
        return None

    labels = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.deleted_at.is_(None),
            AnnotationLabel.status == "active",
        )
        .all()
    )
    by_value = {
        str(row.normalized_label_value or row.label_value).strip().lower(): row
        for row in labels
    }
    by_key: dict[str, list[AnnotationLabel]] = {}
    for row in labels:
        by_key.setdefault(str(row.label_key).strip(), []).append(row)

    resolved: list[AnnotationLabel] = []
    unresolved: list[str] = []
    seen: set[int] = set()
    for attribute in attributes:
        value = str(attribute.get("value") or "").strip().lower()
        key = str(attribute.get("key") or "").strip()
        row = by_value.get(value) if value else None
        if row is None and key and len(by_key.get(key, [])) == 1:
            row = by_key[key][0]
        if row is None:
            unresolved.append(key or value or "<empty>")
            continue
        if int(row.id) in seen:
            continue
        seen.add(int(row.id))
        resolved.append(row)
    if unresolved or not resolved:
        return None
    return create_contract(
        db,
        task=task,
        label_ids=[int(row.id) for row in resolved],
        created_by=created_by,
    )


def create_contract(
    db: Session,
    *,
    task: Task,
    label_ids: Iterable[int],
    created_by: int | None,
    source_preset_id: int | None = None,
    dataset_label_contract_id: int | None = None,
    label_scope_mode: str | None = None,
) -> TaskAnnotationContract:
    labels = active_labels_by_ids(db, label_ids)
    ids = [int(row.id) for row in labels]
    values = [str(row.label_value) for row in labels]
    existing = current_contract(db, int(task.id))
    snapshot = normalize_label_snapshot(labels_snapshot(labels))
    existing_ids = json.loads(existing.label_ids_json) if existing else []
    existing_snapshot = (
        json.loads(existing.labels_snapshot_json or "[]") if existing else []
    )
    if existing and existing_ids == ids and existing_snapshot == snapshot:
        if dataset_label_contract_id is not None:
            existing.dataset_label_contract_id = int(dataset_label_contract_id)
        if label_scope_mode:
            existing.label_scope_mode = str(label_scope_mode).upper()
        db.add(existing)
        db.flush()
        return existing

    has_annotation = (
        db.query(TaskSample.id)
        .filter(
            TaskSample.task_id == int(task.id),
            TaskSample.deleted_at.is_(None),
            TaskSample.state != SampleState.NEW.value,
        )
        .first()
        is not None
    )
    if existing:
        existing.status = "superseded" if not has_annotation else "review_required"

    revision = (
        int(
            db.query(func.max(TaskAnnotationContract.revision))
            .filter(TaskAnnotationContract.task_id == int(task.id))
            .scalar()
            or 0
        )
        + 1
    )
    contract = TaskAnnotationContract(
        task_id=int(task.id),
        revision=revision,
        annotation_kind=contract_annotation_kind(snapshot),
        label_ids_json=json.dumps(ids),
        labels_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        label_set_signature=label_set_signature(ids),
        class_order_signature=class_order_signature(values),
        status="review_required" if has_annotation else "draft",
        source_preset_id=source_preset_id,
        parent_contract_id=existing.id if existing else None,
        dataset_label_contract_id=(
            int(dataset_label_contract_id)
            if dataset_label_contract_id is not None
            else getattr(task, "dataset_label_contract_id", None)
        ),
        label_scope_mode=str(
            label_scope_mode or getattr(task, "task_label_scope_mode", "FULL") or "FULL"
        ).upper(),
        created_by=created_by,
        created_at=datetime.now(),
    )
    db.add(contract)
    db.flush()
    for order, label_id in enumerate(ids):
        db.add(
            TaskAnnotationLabel(
                task_id=int(task.id),
                contract_id=int(contract.id),
                label_id=label_id,
                sort_order=order,
                status="active",
            )
        )
    task.current_annotation_contract_id = int(contract.id)
    if dataset_label_contract_id is not None:
        task.dataset_label_contract_id = int(dataset_label_contract_id)
    if label_scope_mode:
        task.task_label_scope_mode = str(label_scope_mode).upper()
    db.add(task)
    db.flush()
    return contract


def contract_payload(contract: TaskAnnotationContract | None) -> dict[str, Any] | None:
    if not contract:
        return None
    return {
        "id": int(contract.id),
        "task_id": int(contract.task_id),
        "revision": int(contract.revision),
        "annotation_kind": contract.annotation_kind,
        "label_ids": json.loads(contract.label_ids_json or "[]"),
        "labels": json.loads(contract.labels_snapshot_json or "[]"),
        "label_set_signature": contract.label_set_signature,
        "class_order_signature": contract.class_order_signature,
        "status": contract.status,
        "source_preset_id": contract.source_preset_id,
        "dataset_label_contract_id": getattr(contract, "dataset_label_contract_id", None),
        "label_scope_mode": getattr(contract, "label_scope_mode", "FULL") or "FULL",
    }


def contract_history_payloads(db: Session, task_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(TaskAnnotationContract)
        .filter(TaskAnnotationContract.task_id == int(task_id))
        .order_by(TaskAnnotationContract.revision.desc())
        .all()
    )
    return [contract_payload(row) for row in rows]


def upsert_sample_scope(
    db: Session,
    *,
    sample: TaskSample,
    contract: TaskAnnotationContract,
    annotation_data: Any,
    state: str,
    reviewed_by: int | None,
    explicit_negative: bool,
) -> SampleAnnotationScope:
    completion_status = "complete" if state == SampleState.DONE.value else "unprocessed"
    if state == SampleState.INCOMPLETE.value:
        completion_status = "partial"
    elif state == SampleState.ABNORMAL.value:
        completion_status = "invalid"
    label_ids = json.loads(contract.label_ids_json or "[]")
    negative_ids = (
        label_ids if explicit_negative and completion_status == "complete" else []
    )
    row = (
        db.query(SampleAnnotationScope)
        .filter(SampleAnnotationScope.sample_id == int(sample.id))
        .first()
    )
    values = {
        "contract_id": int(contract.id),
        "completion_status": completion_status,
        "explicit_negative": bool(explicit_negative),
        "negative_label_ids_json": json.dumps(negative_ids),
        "annotation_sha256": annotation_sha256(annotation_data),
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.now() if completion_status == "complete" else None,
        "updated_at": datetime.now(),
    }
    if row is None:
        row = SampleAnnotationScope(
            sample_id=int(sample.id), created_at=datetime.now(), **values
        )
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    if contract.status == "draft" and completion_status == "complete":
        contract.status = "active"
    db.flush()
    return row
