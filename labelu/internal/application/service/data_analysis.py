"""Image-data insight for label contracts, labels and global tools.

The page is intentionally problem-oriented: it pairs every contract with a
target, then calculates label and label x global-tool gaps so the result is a
补数 list instead of a plain distribution dashboard.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from typing import Any

from sqlalchemy import func

from labelu.internal.common.error_code import ErrorCode, LabelUException
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
from fastapi import status

MIN_LABEL_TARGET = 30
MAX_LABEL_TARGET = 200
AUTO_TARGET_RATIO = 0.5
MIN_COMBO_TARGET = 3
DIMENSION_ORDER = ["scene_type", "lighting", "angle", "weather", "quality", "source"]


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


def _tag_values(raw_data: Any) -> dict[str, set[str]]:
    data = _json_object(raw_data)
    result_raw = data.get("result")
    result = _json_object(result_raw) if isinstance(result_raw, str) else data
    tags = result.get("tagTool") if isinstance(result, dict) else None
    items = tags.get("result") if isinstance(tags, dict) else None
    if not isinstance(items, list):
        return {}
    values: dict[str, set[str]] = defaultdict(set)
    for item in items:
        item_value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(item_value, dict):
            continue
        for field_key, raw_values in item_value.items():
            options = raw_values if isinstance(raw_values, list) else [raw_values]
            for option in options:
                text = str(option or "").strip()
                if text:
                    values[str(field_key)].add(text)
    return dict(values)


def _contract_groups(db, task_ids: list[int]) -> list[dict[str, Any]]:
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

    groups: dict[str, dict[str, Any]] = {}
    for task in tasks:
        contract = contracts.get(int(task.current_annotation_contract_id or 0))
        group_key = f"contract:{int(contract.id)}" if contract else f"task:{int(task.id)}"
        group = groups.setdefault(
            group_key,
            {
                "contract_id": int(contract.id) if contract else None,
                "contract_revision": int(contract.revision) if contract else None,
                "task_ids": [],
                "task_names": [],
                "image_count": 0,
                "labels": {},
            },
        )
        group["task_ids"].append(int(task.id))
        group["task_names"].append(str(task.name))
        group["image_count"] += images_by_task.get(int(task.id), 0)
        if contract is not None:
            members = [
                member
                for member in memberships.get(int(task.id), [])
                if int(member.contract_id) == int(contract.id)
            ]
        else:
            members = []
        for member in members:
            label = labels.get(int(member.label_id))
            if label is None:
                continue
            value = str(label.label_value or label.label_key or "").strip()
            key = str(label.label_key or "").strip()
            count = max(
                counts_by_task_value.get((int(task.id), value), 0),
                counts_by_task_value.get((int(task.id), key), 0),
            )
            existing = group["labels"].get(value)
            if existing is None:
                group["labels"][value] = {
                    "label_id": int(label.id),
                    "label_key": key,
                    "label_value": value,
                    "color": label.color,
                    "count": count,
                }
            else:
                existing["count"] += count
    return list(groups.values())


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


def _sample_facts(db, task_ids: list[int], task_to_group: dict[int, str]):
    """Load labels/tags once and build per-contract sample sets."""
    if not task_ids:
        return {}, {}, {}, {}
    samples = (
        db.query(TaskSample.id, TaskSample.task_id, TaskSample.data)
        .filter(
            TaskSample.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .all()
    )
    label_rows = (
        db.query(SampleAnnotationLabelIndex.sample_id, SampleAnnotationLabelIndex.label_value)
        .join(TaskSample, TaskSample.id == SampleAnnotationLabelIndex.sample_id)
        .filter(
            SampleAnnotationLabelIndex.task_id.in_(task_ids),
            TaskSample.deleted_at.is_(None),
        )
        .all()
    )
    labels_by_sample: dict[int, set[str]] = defaultdict(set)
    for sample_id, value in label_rows:
        labels_by_sample[int(sample_id)].add(str(value))
    group_labels: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    group_dimensions: dict[str, dict[str, dict[str, set[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set))
    )
    dimension_totals: dict[str, Counter] = defaultdict(Counter)
    for sample_id, task_id, raw_data in samples:
        group_key = task_to_group.get(int(task_id))
        if group_key is None:
            continue
        sample = int(sample_id)
        for value in labels_by_sample.get(sample, set()):
            group_labels[group_key][value].add(sample)
        for field_key, values in _tag_values(raw_data).items():
            for value in values:
                group_dimensions[group_key][field_key][value].add(sample)
                dimension_totals[field_key][value] += 1
    return labels_by_sample, group_labels, group_dimensions, dimension_totals


def _taxonomy(db):
    dimensions = (
        db.query(GlobalTagDimension)
        .filter(GlobalTagDimension.deleted_at.is_(None))
        .order_by(GlobalTagDimension.sort_order.asc())
        .all()
    )
    options = {
        str(row.field_key): []
        for row in dimensions
    }
    option_names: dict[tuple[str, str], str] = {}
    for row in (
        db.query(GlobalTagOption)
        .join(GlobalTagDimension, GlobalTagDimension.id == GlobalTagOption.dimension_id)
        .all()
    ):
        field_key = str(row.dimension.field_key)
        value = str(row.value_en)
        options.setdefault(field_key, []).append(value)
        option_names[(field_key, value)] = row.name_cn
    return dimensions, options, option_names


def _risk_level(score: float) -> str:
    if score < 50:
        return "SEVERE"
    if score < 80:
        return "WARNING"
    return "HEALTHY"


def _priority(actual: int, target: int, gap: int) -> str:
    coverage = actual / target if target else 0
    if actual == 0 or coverage < 0.25:
        return "P0"
    if coverage < 0.6:
        return "P1"
    return "P2"


def _label_target(label_counts: list[int], image_count: int, fixed_target: int) -> int:
    if fixed_target > 0:
        return fixed_target
    average = image_count / max(1, len(label_counts))
    return max(
        MIN_LABEL_TARGET,
        min(MAX_LABEL_TARGET, int(math.ceil(average * AUTO_TARGET_RATIO))),
    )


def get_data_analysis(
    db,
    current_user: User,
    *,
    target_per_label: int = 0,
    contract_id: int | None = None,
) -> dict[str, Any]:
    if not is_task_superuser(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅管理人员可查看数据分析",
        )
    all_task_ids = _task_ids(db, current_user)
    all_groups = _contract_groups(db, all_task_ids)
    contract_options = [
        {
            "contract_id": group["contract_id"],
            "contract_label": " / ".join(group["task_names"]),
            "label_count": len(group["labels"]),
        }
        for group in all_groups
        if group["labels"]
    ]
    groups = all_groups
    if contract_id is not None:
        groups = [
            group for group in groups if int(group["contract_id"] or 0) == int(contract_id)
        ]
    task_ids = sorted(
        {
            int(task_id)
            for group in groups
            for task_id in group["task_ids"]
        }
    )
    fixed_target = max(0, int(target_per_label or 0))
    task_to_group = {
        int(task_id): f"contract:{int(group['contract_id'])}"
        if group["contract_id"] is not None
        else f"task:{int(group['task_ids'][0])}"
        for group in groups
        for task_id in group["task_ids"]
    }
    labels_by_sample, group_labels, group_dimensions, dimension_totals = _sample_facts(
        db, task_ids, task_to_group
    )
    dimensions, dimension_options, option_names = _taxonomy(db)
    used_dimensions = [
        dimension
        for dimension in dimensions
        if dimension_totals.get(str(dimension.field_key))
    ]
    used_dimensions.sort(
        key=lambda item: (
            DIMENSION_ORDER.index(str(item.field_key))
            if str(item.field_key) in DIMENSION_ORDER
            else 99,
            int(item.sort_order or 0),
        )
    )

    label_distribution: list[dict[str, Any]] = []
    contract_health: list[dict[str, Any]] = []
    dimension_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    recommendations: list[dict[str, Any]] = []
    total_labeled_samples: set[int] = set()

    for group in groups:
        if not group["labels"]:
            continue
        task_group_key = (
            f"contract:{int(group['contract_id'])}"
            if group["contract_id"] is not None
            else f"task:{int(group['task_ids'][0])}"
        )
        group_task_ids = {int(value) for value in group["task_ids"]}
        labels = list(group["labels"].values())
        label_target = _label_target(
            [int(item["count"]) for item in labels],
            int(group["image_count"]),
            fixed_target,
        )
        label_rows = []
        for item in labels:
            count = int(item["count"])
            coverage = min(1.0, count / label_target) if label_target else 0
            gap = max(0, label_target - count)
            label_rows.append(
                {
                    **item,
                    "target": label_target,
                    "coverage": round(coverage, 4),
                    "gap": gap,
                }
            )
            if gap:
                recommendations.append(
                    {
                        "priority": _priority(count, label_target, gap),
                        "contract_id": group["contract_id"],
                        "contract_label": " / ".join(group["task_names"]),
                        "task_names": group["task_names"],
                        "label_id": item["label_id"],
                        "label_key": item["label_key"],
                        "label_value": item["label_value"],
                        "dimension_key": "label",
                        "dimension_name": "标签总量",
                        "value": "__all__",
                        "value_name": "全部图片",
                        "actual": count,
                        "target": label_target,
                        "gap": gap,
                        "coverage": round(coverage, 4),
                    }
                )
            label_distribution.append(
                {
                    **item,
                    "target": label_target,
                    "coverage": round(coverage, 4),
                    "gap": gap,
                    "contract_names": group["task_names"],
                }
            )
        label_rows.sort(key=lambda item: (-item["gap"], -item["count"]))

        combo_cells: list[dict[str, Any]] = []
        for dimension in used_dimensions:
            field_key = str(dimension.field_key)
            observed = dimension_totals.get(field_key, Counter())
            values = [
                value
                for value in dimension_options.get(field_key, [])
                if observed.get(value, 0) > 0
            ]
            for value in observed:
                if value not in values:
                    values.append(value)
            if not values:
                continue
            combo_target = max(
                MIN_COMBO_TARGET,
                int(math.ceil(label_target / max(1, len(values)))),
            )
            for label in label_rows:
                # A label with zero images is already represented by the
                # label-total recommendation. Cross-tab rows only make sense
                # once the label actually exists in the contract.
                if int(label["count"]) <= 0:
                    continue
                row = {
                    "contract_id": group["contract_id"],
                    "contract_revision": group["contract_revision"],
                    "contract_label": " / ".join(group["task_names"]),
                    "task_names": group["task_names"],
                    "label_id": label["label_id"],
                    "label_key": label["label_key"],
                    "label_value": label["label_value"],
                    "color": label["color"],
                    "label_count": label["count"],
                    "label_target": label_target,
                    "cells": {},
                }
                for value in values:
                    actual = len(
                        group_labels[task_group_key].get(label["label_value"], set())
                        & group_dimensions[task_group_key][field_key].get(value, set())
                    )
                    coverage = min(1.0, actual / combo_target) if combo_target else 0
                    gap = max(0, combo_target - actual)
                    cell = {
                        "actual": actual,
                        "target": combo_target,
                        "coverage": round(coverage, 4),
                        "gap": gap,
                    }
                    row["cells"][value] = cell
                    combo_cells.append(cell)
                    if gap:
                        recommendations.append(
                            {
                                "priority": _priority(actual, combo_target, gap),
                                "contract_id": group["contract_id"],
                                "contract_label": " / ".join(group["task_names"]),
                                "task_names": group["task_names"],
                                "label_id": label["label_id"],
                                "label_key": label["label_key"],
                                "label_value": label["label_value"],
                                "dimension_key": field_key,
                                "dimension_name": dimension.name_cn,
                                "value": value,
                                "value_name": option_names.get((field_key, value), value),
                                "actual": actual,
                                "target": combo_target,
                                "gap": gap,
                                "coverage": round(coverage, 4),
                            }
                        )
                dimension_rows[field_key].append(row)

        label_coverage = (
            sum(item["coverage"] for item in label_rows) / len(label_rows)
            if label_rows
            else 0
        )
        combo_coverage = (
            sum(cell["coverage"] for cell in combo_cells) / len(combo_cells)
            if combo_cells
            else 0
        )
        health_score = round((label_coverage * 0.55 + combo_coverage * 0.45) * 100, 1)
        contract_gap_count = len([item for item in recommendations if item["contract_id"] == group["contract_id"] and item["contract_label"] == " / ".join(group["task_names"])])
        contract_health.append(
            {
                "contract_id": group["contract_id"],
                "contract_revision": group["contract_revision"],
                "contract_label": " / ".join(group["task_names"]),
                "task_names": group["task_names"],
                "task_count": len(group["task_ids"]),
                "image_count": group["image_count"],
                "label_count": len(label_rows),
                "label_target": label_target,
                "label_coverage": round(label_coverage, 4),
                "combo_coverage": round(combo_coverage, 4),
                "health_score": health_score,
                "risk_level": _risk_level(health_score),
                "gap_count": contract_gap_count,
                "severe_gap_count": len(
                    [
                        item
                        for item in recommendations
                        if item["contract_id"] == group["contract_id"]
                        and item["contract_label"] == " / ".join(group["task_names"])
                        and item["priority"] == "P0"
                    ]
                ),
                "recommended_images": sum(
                    item["gap"]
                    for item in recommendations
                    if item["contract_id"] == group["contract_id"]
                    and item["contract_label"] == " / ".join(group["task_names"])
                    and item["dimension_key"] == "label"
                ),
            }
        )
        total_labeled_samples.update(
            sample
            for values in group_labels[task_group_key].values()
            for sample in values
        )

    contract_health.sort(key=lambda item: item["health_score"])
    recommendations.sort(
        key=lambda item: (
            {"P0": 0, "P1": 1, "P2": 2}.get(item["priority"], 3),
            -item["gap"],
            item["contract_label"],
            item["label_key"],
            item["dimension_name"],
        )
    )
    gaps = recommendations[:200]
    context_for_dimension = [
        {
            "field_key": str(dimension.field_key),
            "name_cn": dimension.name_cn,
            "values": [
                {
                    "value": value,
                    "name_cn": option_names.get((str(dimension.field_key), value), value),
                    "total_count": int(dimension_totals[str(dimension.field_key)].get(value, 0)),
                }
                for value in (
                    [
                        value
                        for value in dimension_options.get(str(dimension.field_key), [])
                        if dimension_totals[str(dimension.field_key)].get(value, 0) > 0
                    ]
                    + [
                        value
                        for value in dimension_totals[str(dimension.field_key)]
                        if value not in dimension_options.get(str(dimension.field_key), [])
                    ]
                )
            ],
            "rows": dimension_rows.get(str(dimension.field_key), []),
        }
        for dimension in used_dimensions
    ]
    state_rows = _state_counts(db, task_ids)
    total_images = sum(item["count"] for item in state_rows)
    severe_gap_count = len(
        [
            item
            for item in recommendations
            if item["dimension_key"] == "label" and item["priority"] == "P0"
        ]
    )
    label_gap_count = len(
        [item for item in recommendations if item["dimension_key"] == "label"]
    )
    combo_gap_count = len(
        [item for item in recommendations if item["dimension_key"] != "label"]
    )
    risk_score = (
        round(sum(item["health_score"] for item in contract_health) / len(contract_health), 1)
        if contract_health
        else 0
    )
    return {
        "summary": {
            "total_images": total_images,
            "labeled_images": len(total_labeled_samples),
            "labeled_rate": round(len(total_labeled_samples) / total_images, 4)
            if total_images
            else 0,
            "contract_count": len(contract_health),
            "label_count": len({item["label_id"] for item in label_distribution}),
            "combo_gap_count": combo_gap_count,
            "label_gap_count": label_gap_count,
            "severe_gap_count": severe_gap_count,
            "recommended_images": sum(
                item["gap"]
                for item in recommendations
                if item["dimension_key"] == "label"
            ),
            "risk_level": _risk_level(risk_score),
            "target_mode": "FIXED" if fixed_target else "AUTO",
            "target_per_label": fixed_target,
        },
        "state_counts": state_rows,
        "label_distribution": [
            item
            for item in sorted(
                label_distribution,
                key=lambda item: (-item["gap"], -item["count"]),
            )
        ],
        "contract_health": contract_health,
        "contract_options": contract_options,
        "dimensions": context_for_dimension,
        "recommendations": gaps,
        "gaps": gaps,
        "global_tools": _global_tool_counts(db, task_ids),
        "contracts": contract_health,
    }


def _global_tool_counts(db, task_ids: list[int]) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    _, options, option_names = _taxonomy(db)
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
        for field_key, values in _tag_values(raw).items():
            for value in values:
                counts[field_key][value] += 1
    dimensions = {
        str(row.field_key): row
        for row in db.query(GlobalTagDimension)
        .filter(GlobalTagDimension.deleted_at.is_(None))
        .all()
    }
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
