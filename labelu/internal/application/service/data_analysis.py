"""Image-data insight for label contracts, labels and global tools."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from typing import Any

from sqlalchemy import func

from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationLabelIndex,
    TaskAnnotationContract,
    TaskAnnotationLabel,
)
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    GlobalTagDimension,
    GlobalTagOption,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.common.error_code import ErrorCode, LabelUException
from fastapi import status


def _task_ids(db, current_user: User) -> list[int]:
    query = crud_task.visible_query(
        db=db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    ).filter(Task.media_type == "IMAGE")
    return [int(row[0]) for row in query.with_entities(Task.id).all()]


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _contract_labels(db, task_ids: list[int]) -> list[dict[str, Any]]:
    tasks = (
        db.query(Task)
        .filter(Task.id.in_(task_ids), Task.deleted_at.is_(None))
        .order_by(Task.id.asc())
        .all()
    )
    if not tasks:
        return []
    contracts = {
        int(row.id): row
        for row in db.query(TaskAnnotationContract)
        .filter(TaskAnnotationContract.task_id.in_(task_ids))
        .all()
    }
    images_by_task = {
        int(task_id): int(count)
        for task_id, count in db.query(TaskSample.task_id, func.count(TaskSample.id))
        .filter(
            TaskSample.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .group_by(TaskSample.task_id)
        .all()
    }
    index_rows = (
        db.query(
            SampleAnnotationLabelIndex.task_id,
            SampleAnnotationLabelIndex.label_value,
            func.count(func.distinct(SampleAnnotationLabelIndex.sample_id)),
        )
        .join(TaskSample, TaskSample.id == SampleAnnotationLabelIndex.sample_id)
        .filter(
            SampleAnnotationLabelIndex.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .group_by(
            SampleAnnotationLabelIndex.task_id,
            SampleAnnotationLabelIndex.label_value,
        )
        .all()
    )
    counts_by_task_value = {
        (int(task_id), str(value)): int(count)
        for task_id, value, count in index_rows
    }
    labels = {
        int(label.id): label
        for label in db.query(AnnotationLabel)
        .filter(AnnotationLabel.deleted_at.is_(None))
        .all()
    }
    memberships: dict[int, list[TaskAnnotationLabel]] = defaultdict(list)
    for row in (
        db.query(TaskAnnotationLabel)
        .filter(
            TaskAnnotationLabel.task_id.in_(task_ids),
            TaskAnnotationLabel.status == "active",
        )
        .order_by(TaskAnnotationLabel.sort_order.asc(), TaskAnnotationLabel.id.asc())
        .all()
    ):
        memberships[int(row.task_id)].append(row)

    result = []
    for task in tasks:
        contract = contracts.get(int(task.current_annotation_contract_id or 0))
        if contract is not None:
            task_members = [
                member
                for member in memberships.get(int(task.id), [])
                if int(member.contract_id) == int(contract.id)
            ]
        else:
            task_members = []
        task_image_count = images_by_task.get(int(task.id), 0)
        label_rows = []
        for member in task_members:
            label = labels.get(int(member.label_id))
            if label is None:
                continue
            value = str(label.label_value or label.label_key or "").strip()
            key = str(label.label_key or "").strip()
            count = max(
                counts_by_task_value.get((int(task.id), value), 0),
                counts_by_task_value.get((int(task.id), key), 0),
            )
            label_rows.append(
                {
                    "label_id": int(label.id),
                    "label_key": key,
                    "label_value": value,
                    "color": label.color,
                    "count": count,
                }
            )
        result.append(
            {
                "contract_id": int(contract.id) if contract else None,
                "contract_revision": int(contract.revision) if contract else None,
                "task_id": int(task.id),
                "task_name": task.name,
                "image_count": task_image_count,
                "labels": label_rows,
            }
        )
    return result


def _state_counts(db, task_ids: list[int]) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    rows = (
        db.query(TaskSample.state, func.count(TaskSample.id))
        .filter(
            TaskSample.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .group_by(TaskSample.state)
        .all()
    )
    return [
        {"state": str(state or "NEW"), "count": int(count)}
        for state, count in rows
    ]


def _global_tool_counts(db, task_ids: list[int]) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    dimensions = {
        str(row.field_key): row
        for row in db.query(GlobalTagDimension)
        .filter(GlobalTagDimension.deleted_at.is_(None))
        .order_by(GlobalTagDimension.sort_order.asc())
        .all()
    }
    option_names = {
        (str(row.dimension.field_key), str(row.value_en)): row.name_cn
        for row in db.query(GlobalTagOption)
        .join(GlobalTagDimension, GlobalTagDimension.id == GlobalTagOption.dimension_id)
        .all()
    }
    counts: dict[str, Counter] = defaultdict(Counter)
    samples = (
        db.query(TaskSample.data)
        .filter(
            TaskSample.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .all()
    )
    for (raw,) in samples:
        data = _json_object(raw)
        result = _json_object(data.get("result")) if isinstance(data.get("result"), str) else data
        tags = result.get("tagTool") if isinstance(result, dict) else None
        items = tags.get("result") if isinstance(tags, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            values = item.get("value") if isinstance(item, dict) else None
            if not isinstance(values, dict):
                continue
            for field_key, raw_values in values.items():
                options = raw_values if isinstance(raw_values, list) else [raw_values]
                for option in options:
                    if option is None or str(option).strip() == "":
                        continue
                    counts[str(field_key)][str(option)] += 1

    result = []
    for field_key in sorted(counts):
        dimension = dimensions.get(field_key)
        result.append(
            {
                "field_key": field_key,
                "name_cn": dimension.name_cn if dimension else field_key,
                "values": [
                    {
                        "value": value,
                        "name_cn": option_names.get((field_key, value), value),
                        "count": count,
                    }
                    for value, count in counts[field_key].most_common(20)
                ],
            }
        )
    return result


def get_data_analysis(
    db,
    current_user: User,
    *,
    target_per_label: int = 100,
) -> dict[str, Any]:
    if not is_task_superuser(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅管理人员可查看数据分析",
        )
    task_ids = _task_ids(db, current_user)
    contracts = _contract_labels(db, task_ids)
    target = max(1, int(target_per_label or 100))
    gap_rows = []
    label_distribution: Counter = Counter()
    label_meta: dict[str, dict[str, Any]] = {}
    contract_summary = []
    for contract in contracts:
        labels = []
        gap_count = 0
        for row in contract["labels"]:
            count = int(row["count"])
            gap = max(0, target - count)
            label_distribution[row["label_value"]] += count
            label_meta[row["label_value"]] = {
                "label_key": row["label_key"],
                "label_value": row["label_value"],
                "color": row["color"],
            }
            labels.append(
                {
                    **row,
                    "target": target,
                    "coverage": round(min(1.0, count / target), 4),
                    "gap": gap,
                }
            )
            if gap:
                gap_count += 1
                gap_rows.append(
                    {
                        "contract_id": contract["contract_id"],
                        "task_id": contract["task_id"],
                        "task_name": contract["task_name"],
                        "label_id": row["label_id"],
                        "label_key": row["label_key"],
                        "label_value": row["label_value"],
                        "count": count,
                        "target": target,
                        "gap": gap,
                    }
                )
        contract_summary.append(
            {
                **contract,
                "labels": labels,
                "gap_count": gap_count,
                "coverage": round(
                    sum(item["coverage"] for item in labels) / len(labels), 4
                )
                if labels
                else 0,
            }
        )
    gap_rows.sort(key=lambda item: (-item["gap"], item["task_name"], item["label_key"]))
    state_rows = _state_counts(db, task_ids)
    total_images = sum(item["count"] for item in state_rows)
    labeled_images = sum(
        1
        for _ in db.query(SampleAnnotationLabelIndex.sample_id)
        .join(TaskSample, TaskSample.id == SampleAnnotationLabelIndex.sample_id)
        .filter(
            SampleAnnotationLabelIndex.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .distinct()
        .all()
    )
    return {
        "summary": {
            "total_images": total_images,
            "labeled_images": labeled_images,
            "contract_count": len(contracts),
            "label_count": len(label_distribution),
            "gap_count": len(gap_rows),
            "target_per_label": target,
        },
        "state_counts": state_rows,
        "label_distribution": [
            {**label_meta[value], "count": count}
            for value, count in label_distribution.most_common(30)
        ],
        "contracts": contract_summary,
        "gaps": gap_rows[:200],
        "global_tools": _global_tool_counts(db, task_ids),
    }
