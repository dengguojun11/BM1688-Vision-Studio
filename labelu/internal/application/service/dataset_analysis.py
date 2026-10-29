"""Aggregate image and annotation distributions for a dataset category."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.application.response.dataset_analysis import (
    DatasetAnalysisResponse,
    DatasetAnalysisTaskRow,
    DatasetAnalysisTotals,
    DatasetDimensionDistribution,
    DatasetDistributionItem,
    DatasetGapRecommendation,
    DatasetLabelDistributionItem,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_image import parse_sample_result
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task

_DIMENSION_DEFINITIONS = (
    ("sceneType", "场景类型", ("sceneType", "scene_type", "scene")),
    ("weather", "天气环境", ("weather", "weather_environment")),
    (
        "lighting",
        "光照/拍摄时间",
        ("lighting", "light", "time", "shooting_time", "capture_time"),
    ),
    ("quality", "图片质量", ("quality", "image_quality")),
    ("angle", "拍摄角度", ("angle", "camera_angle", "shooting_angle")),
    ("source", "数据来源", ("source", "data_source", "source_type")),
)
_DIMENSION_KEYS = {key for key, _, _ in _DIMENSION_DEFINITIONS}
_LABEL_MIN_IMAGE_SHARE = 0.10
_TEST_SPLIT_RATIO = 0.10
_MIN_TEST_INSTANCES = 20


def _parse_task_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_values(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    values = raw if isinstance(raw, list) else [raw]
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("value") or value.get("key")
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _tag_values(result: dict[str, Any]) -> dict[str, list[str]]:
    block = result.get("tagTool")
    items = block.get("result") if isinstance(block, dict) else []
    if not isinstance(items, list):
        return {}
    values: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = item.get("value")
        if not isinstance(payload, dict):
            legacy = item.get("result")
            payload = legacy if isinstance(legacy, dict) else {}
        for key, raw in payload.items():
            for value in _as_values(raw):
                if value not in values[str(key)]:
                    values[str(key)].append(value)
    return dict(values)


def _canonical_dimension(key: str) -> str | None:
    normalized = str(key or "").strip()
    for canonical, _, aliases in _DIMENSION_DEFINITIONS:
        if normalized in aliases:
            return canonical
    return None


def _task_metadata(
    task: Task,
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """Return tag display names, expected values, and configured rect labels."""
    display: dict[str, str] = {}
    expected: dict[str, list[str]] = defaultdict(list)
    rect_labels: list[str] = []
    config = _parse_task_config(task.config)
    for tool in config.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        tool_name = tool.get("tool")
        attrs = (tool.get("config") or {}).get("attributes", []) or []
        if not isinstance(attrs, list):
            continue
        if tool_name == "rectTool":
            for attr in attrs:
                if not isinstance(attr, dict):
                    continue
                value = str(attr.get("value") or attr.get("key") or "").strip()
                if value and value not in rect_labels:
                    rect_labels.append(value)
        elif tool_name == "tagTool":
            for attr in attrs:
                if not isinstance(attr, dict):
                    continue
                raw_key = str(attr.get("value") or attr.get("key") or "").strip()
                canonical = _canonical_dimension(raw_key)
                if not canonical:
                    continue
                display[canonical] = str(attr.get("key") or canonical).strip()
                for option in attr.get("options", []) or []:
                    if not isinstance(option, dict):
                        continue
                    value = str(option.get("value") or option.get("key") or "").strip()
                    if value and value not in expected[canonical]:
                        expected[canonical].append(value)
    return display, dict(expected), rect_labels


def _source_for_task(task: Task) -> str:
    return (
        str(
            getattr(task, "source_system", None)
            or getattr(task, "source_type", None)
            or "未标注"
        ).strip()
        or "未标注"
    )


def _percentage(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0


def _priority(current: int, target: int) -> str:
    if current <= 0:
        return "critical"
    if current < target:
        return "warning"
    return "ok"


def _coverage_target(total_images: int) -> int:
    return max(5, math.ceil(total_images * 0.05)) if total_images else 0


def _label_target(
    *,
    total_images: int,
    image_count: int,
    instance_count: int,
) -> int:
    if total_images <= 0:
        return 0
    dataset_share_target = math.ceil(total_images * _LABEL_MIN_IMAGE_SHARE)
    instances_per_image = (
        instance_count / image_count if image_count > 0 and instance_count > 0 else 1.0
    )
    evaluation_target = math.ceil(
        _MIN_TEST_INSTANCES / (_TEST_SPLIT_RATIO * instances_per_image)
    )
    return max(dataset_share_target, evaluation_target)


def _build_recommendations(
    dimensions: list[DatasetDimensionDistribution],
    labels: list[DatasetLabelDistributionItem],
    expected_labels: set[str],
    total_images: int,
) -> list[DatasetGapRecommendation]:
    dimension_target = _coverage_target(total_images)
    recommendations: list[DatasetGapRecommendation] = []
    for dimension in dimensions:
        for value in dimension.missing_values:
            recommendations.append(
                DatasetGapRecommendation(
                    dimension=dimension.key,
                    dimension_label=dimension.label,
                    value=value,
                    current_count=0,
                    target_count=dimension_target,
                    priority="critical",
                    message=f"建议补充{dimension.label}为“{value}”的图片",
                )
            )
        for item in dimension.items:
            if dimension.expected_values and item.count < dimension_target:
                recommendations.append(
                    DatasetGapRecommendation(
                        dimension=dimension.key,
                        dimension_label=dimension.label,
                        value=item.value,
                        current_count=item.count,
                        target_count=dimension_target,
                        priority=_priority(item.count, dimension_target),
                        message=f"{dimension.label}“{item.value}”样本偏少，建议至少补充到 {dimension_target} 张",
                    )
                )
    for item in labels:
        if item.gap_count <= 0:
            continue
        recommendations.append(
            DatasetGapRecommendation(
                dimension="label",
                dimension_label="目标标签",
                value=item.label,
                current_count=item.image_count,
                target_count=item.target_count,
                priority=_priority(item.image_count, item.target_count),
                message=(
                    f"目标标签“{item.label}”当前有 {item.image_count} 张图片，"
                    f"建议新增 {item.gap_count} 张，达到 {item.target_count} 张"
                ),
            )
        )
    observed_labels = {item.label for item in labels}
    for value in sorted(expected_labels - observed_labels):
        target = _label_target(
            total_images=total_images,
            image_count=0,
            instance_count=0,
        )
        recommendations.append(
            DatasetGapRecommendation(
                dimension="label",
                dimension_label="目标标签",
                value=value,
                current_count=0,
                target_count=target,
                priority="critical",
                message=f"目标标签“{value}”当前没有可见图片，建议补充标注样本",
            )
        )
    rank = {"critical": 0, "warning": 1, "ok": 2}
    recommendations.sort(
        key=lambda item: (
            rank.get(item.priority, 9),
            0 if item.dimension == "label" else 1,
            item.current_count,
            item.value,
        )
    )
    return recommendations[:24]


def build_dataset_analysis(
    db: Session,
    current_user,
    *,
    dataset_category_id: int | None = None,
    dataset_id: int | None = None,
    task_ids: list[int] | None = None,
) -> DatasetAnalysisResponse:
    dataset = None
    category = None
    if dataset_id is not None:
        dataset = (
            db.query(Dataset)
            .filter(
                Dataset.id == int(dataset_id),
                Dataset.deleted_at.is_(None),
                Dataset.status == "ACTIVE",
            )
            .first()
        )
        if dataset is None:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=404,
                message="数据集不存在或已停用",
            )
        if not is_task_superuser(current_user) and int(dataset.owner_id) != int(
            current_user.id
        ):
            visible_dataset_task = (
                crud_task.visible_query(
                    db,
                    owner_id=current_user.id,
                    list_all_tasks=False,
                )
                .filter(Task.dataset_id == int(dataset.id))
                .first()
            )
            if visible_dataset_task is None:
                raise LabelUException(
                    code=ErrorCode.CODE_30001_NO_PERMISSION,
                    status_code=403,
                    message="无权访问该数据集",
                )
    elif dataset_category_id is not None:
        # Historical clients may still request category-scoped analysis. This
        # path is read-only compatibility and is not exposed by new pages.
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
                status_code=400,
                message="数据集分类不存在或已停用",
            )
    else:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message="必须指定数据集",
        )

    requested_task_ids = {
        int(task_id)
        for task_id in (task_ids or [])
        if int(task_id) > 0
    }
    tasks_query = (
        crud_task.visible_query(
            db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        .filter(
            Task.dataset_id == int(dataset_id)
            if dataset_id is not None
            else Task.dataset_category_id == int(dataset_category_id)
        )
    )
    if requested_task_ids:
        tasks_query = tasks_query.filter(Task.id.in_(requested_task_ids))
    tasks = tasks_query.order_by(Task.id.desc()).all()
    image_tasks = [task for task in tasks if task.media_type == MediaType.IMAGE.value]
    task_ids = [int(task.id) for task in image_tasks]
    task_map = {int(task.id): task for task in image_tasks}

    dimension_counts: dict[str, Counter[str]] = {
        key: Counter() for key, _, _ in _DIMENSION_DEFINITIONS
    }
    dimension_covered: Counter[str] = Counter()
    label_images: Counter[str] = Counter()
    label_instances: Counter[str] = Counter()
    task_counts: dict[int, Counter[str]] = {task_id: Counter() for task_id in task_ids}
    display_names: dict[str, str] = {}
    expected_values: dict[str, set[str]] = defaultdict(set)
    expected_labels: set[str] = set()
    task_tag_keys: dict[int, dict[str, list[str]]] = {}
    for task in image_tasks:
        display, expected, rect_labels = _task_metadata(task)
        display_names.update(display)
        for key, values in expected.items():
            expected_values[key].update(values)
        expected_labels.update(rect_labels)
        task_tag_keys[int(task.id)] = expected

    total_images = done_images = pending_images = tagged_images = labeled_images = (
        label_instance_count
    ) = 0
    if task_ids:
        samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id.in_(task_ids),
                TaskSample.deleted_at.is_(None),
            )
            .order_by(TaskSample.id.asc())
            .yield_per(1000)
        )
        for sample in samples:
            task = task_map.get(int(sample.task_id))
            if task is None:
                continue
            task_id = int(task.id)
            total_images += 1
            state = str(sample.state or "")
            if state == SampleState.DONE.value:
                done_images += 1
            else:
                pending_images += 1
            _, result = parse_sample_result(sample.data)
            tags = _tag_values(result)
            canonical_tags: dict[str, list[str]] = defaultdict(list)
            for raw_key, values in tags.items():
                canonical = _canonical_dimension(raw_key)
                if canonical:
                    for value in values:
                        if value not in canonical_tags[canonical]:
                            canonical_tags[canonical].append(value)
            if canonical_tags:
                tagged_images += 1
            for key, values in canonical_tags.items():
                dimension_covered[key] += 1
                for value in values:
                    dimension_counts[key][value] += 1

            rect_tool = result.get("rectTool")
            rects = rect_tool.get("result") if isinstance(rect_tool, dict) else []
            if not isinstance(rects, list):
                rects = []
            sample_labels: set[str] = set()
            for rect in rects:
                if not isinstance(rect, dict):
                    continue
                label = str(rect.get("label") or "").strip()
                if label:
                    sample_labels.add(label)
                    label_instances[label] += 1
            for label in sample_labels:
                label_images[label] += 1
            if sample_labels:
                labeled_images += 1
            task_counts[task_id]["total"] += 1
            task_counts[task_id]["done"] += int(state == SampleState.DONE.value)
            task_counts[task_id]["pending"] += int(state != SampleState.DONE.value)
            task_counts[task_id]["tagged"] += int(bool(canonical_tags))
            task_counts[task_id]["labeled"] += int(bool(sample_labels))

            if "source" not in canonical_tags:
                dimension_counts["source"][_source_for_task(task)] += 1
                dimension_covered["source"] += 1

    dimensions: list[DatasetDimensionDistribution] = []
    for key, default_label, _ in _DIMENSION_DEFINITIONS:
        label = display_names.get(key) or default_label
        counts = dimension_counts[key]
        sorted_items = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        expected = sorted(expected_values.get(key, set()))
        display_map: dict[str, str] = {}
        for task in image_tasks:
            config = _parse_task_config(task.config)
            for tool in config.get("tools", []) or []:
                if not isinstance(tool, dict) or tool.get("tool") != "tagTool":
                    continue
                for attr in (tool.get("config") or {}).get("attributes", []) or []:
                    if (
                        not isinstance(attr, dict)
                        or _canonical_dimension(
                            str(attr.get("value") or attr.get("key") or "")
                        )
                        != key
                    ):
                        continue
                    for option in attr.get("options", []) or []:
                        if isinstance(option, dict):
                            raw = str(
                                option.get("value") or option.get("key") or ""
                            ).strip()
                            name = str(option.get("key") or raw).strip()
                            if raw and name:
                                display_map[raw] = name
        visible_counts = [
            (display_map.get(value, value), count) for value, count in sorted_items
        ]
        expected_display = [display_map.get(value, value) for value in expected]
        observed_display = {value for value, _ in visible_counts}
        items = [
            DatasetDistributionItem(
                value=value,
                count=count,
                percentage=_percentage(count, total_images),
            )
            for value, count in visible_counts
        ]
        dimensions.append(
            DatasetDimensionDistribution(
                key=key,
                label=label,
                total_images=total_images,
                covered_images=dimension_covered[key],
                missing_images=max(0, total_images - dimension_covered[key]),
                items=items,
                expected_values=expected_display,
                missing_values=sorted(set(expected_display) - observed_display),
            )
        )

    labels = [
        DatasetLabelDistributionItem(
            label=label,
            image_count=count,
            instance_count=label_instances[label],
            percentage=_percentage(count, total_images),
            target_count=(
                target := _label_target(
                    total_images=total_images,
                    image_count=count,
                    instance_count=label_instances[label],
                )
            ),
            gap_count=max(0, target - count),
        )
        for label, count in sorted(
            label_images.items(), key=lambda pair: (-pair[1], pair[0])
        )
    ]
    task_rows = []
    for task in image_tasks:
        counts = task_counts[int(task.id)]
        task_rows.append(
            DatasetAnalysisTaskRow(
                task_id=int(task.id),
                task_name=str(task.name or f"任务 #{task.id}"),
                status=task.status,
                source=_source_for_task(task),
                total_images=counts["total"],
                done_images=counts["done"],
                pending_images=counts["pending"],
                labeled_images=counts["labeled"],
                tag_coverage=_percentage(counts["tagged"], counts["total"]),
            )
        )

    return DatasetAnalysisResponse(
        category_id=int(category.id) if category is not None else 0,
        category_key=str(category.category_key) if category is not None else "",
        category_name=str(category.name) if category is not None else "",
        dataset_id=int(dataset.id) if dataset is not None else None,
        dataset_name=str(dataset.name) if dataset is not None else None,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        totals=DatasetAnalysisTotals(
            task_count=len(tasks),
            image_task_count=len(image_tasks),
            total_images=total_images,
            done_images=done_images,
            pending_images=pending_images,
            tagged_images=tagged_images,
            labeled_images=labeled_images,
            label_instance_count=label_instance_count or sum(label_instances.values()),
        ),
        dimensions=dimensions,
        labels=labels,
        tasks=task_rows,
        recommendations=_build_recommendations(
            dimensions,
            labels,
            expected_labels,
            total_images,
        ),
    )
