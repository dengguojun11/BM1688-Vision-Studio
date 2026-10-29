"""多标注任务合并为单一 YOLO 训练数据集。"""
from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_annotation_taxonomy, crud_task
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.service.yolo_dataset import (
    build_yolo_rect_class_names,
    rect_class_names_match,
    validate_merge_tasks_sample_labels,
    validate_rect_config_compatible,
    validate_yolo_training_prerequisites,
)
from labelu.internal.application.service.task_training_ready import (
    ensure_task_training_ready_refreshed,
)
from labelu.internal.application.service.training_review_gate import filter_trainable_samples
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User

MAX_MERGE_TASKS = 30


def normalize_source_task_ids(primary_task_id: int, merge_task_ids: Optional[List[int]]) -> List[int]:
    out: List[int] = [int(primary_task_id)]
    seen = {out[0]}
    for raw in merge_task_ids or []:
        tid = int(raw)
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    if len(out) > MAX_MERGE_TASKS:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"合并训练最多支持 {MAX_MERGE_TASKS} 个任务",
        )
    return out


def source_task_ids_json(ids: List[int]) -> str:
    return json.dumps(ids, ensure_ascii=False)


def parse_source_task_ids(job) -> List[int]:
    raw = getattr(job, "source_task_ids_json", None)
    if not raw:
        tid = getattr(job, "task_id", None)
        return [int(tid)] if tid is not None else []
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return [int(x) for x in data]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    tid = getattr(job, "task_id", None)
    return [int(tid)] if tid is not None else []


def merge_task_label(ids: List[int]) -> str:
    if not ids:
        return ""
    if len(ids) == 1:
        return f"#{ids[0]}"
    return "#" + "+".join(str(i) for i in ids)


def rect_classes_from_label_preset(preset) -> List[str]:
    """与 annotation-settings 预设项顺序一致（sort_order, id）。"""
    items = sorted(
        list(preset.items or []),
        key=lambda x: (int(getattr(x, "sort_order", 0) or 0), int(x.id)),
    )
    out: List[str] = []
    for item in items:
        v = (getattr(item, "label_value", None) or getattr(item, "label_key", None) or "")
        v = str(v).strip()
        if v:
            out.append(v)
    return out


def task_matches_rect_preset_slug(task_config: dict, *, preset_slug: str) -> bool:
    applied = (task_config.get("defaultRectPresetId") or "").strip()
    return bool(applied) and applied == (preset_slug or "").strip()


def _parse_task_config(config_raw: Any) -> dict:
    if not config_raw:
        return {}
    try:
        data = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def load_merged_training_context(
    db: Session,
    *,
    primary_task_id: int,
    source_task_ids: List[int],
    current_user: User,
    rect_preset_slug: Optional[str] = None,
    frozen_samples_by_task: Optional[dict[int, list[Any]]] = None,
) -> Tuple[Task, dict, List[TaskSample], dict]:
    """
    校验多任务可合并，返回 (主任务, 主任务 config, 全部 DONE 样本, 每任务样本数统计)。
    """
    primary = crud_task.get(db=db, task_id=primary_task_id)
    if not primary:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(primary, current_user)

    if frozen_samples_by_task is None and primary.media_type == MediaType.IMAGE.value:
        ensure_task_training_ready_refreshed(db, primary)

    if primary.status == TaskStatus.DRAFT.value:
        raise LabelUException(
            code=ErrorCode.CODE_57001_YOLO_TRAIN_TASK_NOT_FINISHED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="主任务仍为草稿（DRAFT），请先完成配置与标注。",
        )
    if primary.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_57005_YOLO_TRAIN_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    primary_config = _parse_task_config(primary.config)
    preset_slug = (rect_preset_slug or "").strip()
    preset_row = None
    if preset_slug:
        preset_row = crud_annotation_taxonomy.label_preset_get_by_slug(db, preset_slug)
        if not preset_row:
            raise LabelUException(
                code=ErrorCode.CODE_57026_YOLO_TRAIN_MERGE_INCOMPATIBLE,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"拉框预设 slug={preset_slug} 不存在，请先在标注设置中创建",
            )
        ref_classes = rect_classes_from_label_preset(preset_row)
        if not ref_classes:
            raise LabelUException(
                code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"拉框预设「{preset_row.name}」没有标签项",
            )
    else:
        ref_classes = build_yolo_rect_class_names(primary_config)
        if not ref_classes:
            raise LabelUException(
                code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="主任务 rectTool 未配置类别，无法训练。",
            )

    all_samples: List[TaskSample] = []
    per_task_counts: dict[int, int] = {}
    per_task_samples: dict[int, List[TaskSample]] = {}

    for tid in source_task_ids:
        task = primary if tid == primary_task_id else crud_task.get(db=db, task_id=tid)
        if not task:
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
                message=f"任务 #{tid} 不存在",
            )
        assert_user_has_task_access(task, current_user)
        if frozen_samples_by_task is None and task.media_type == MediaType.IMAGE.value:
            ensure_task_training_ready_refreshed(db, task)
        if task.status == TaskStatus.DRAFT.value:
            raise LabelUException(
                code=ErrorCode.CODE_57001_YOLO_TRAIN_TASK_NOT_FINISHED,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"任务 #{tid} 仍为草稿，无法参与合并训练",
            )
        if task.media_type != MediaType.IMAGE.value:
            raise LabelUException(
                code=ErrorCode.CODE_57005_YOLO_TRAIN_UNSUPPORTED_MEDIA,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"任务 #{tid} 不是图像任务",
            )
        task_config = _parse_task_config(task.config)
        if preset_slug:
            if not (
                task_matches_rect_preset_slug(task_config, preset_slug=preset_slug)
                or rect_class_names_match(
                    ref_classes, build_yolo_rect_class_names(task_config)
                )
            ):
                raise LabelUException(
                    code=ErrorCode.CODE_57026_YOLO_TRAIN_MERGE_INCOMPATIBLE,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    message=(
                        f"任务 #{tid} 未应用拉框预设「{preset_row.name}」"
                        f"（slug={preset_slug}），请在任务中应用该预设后再训练"
                    ),
                )
        else:
            try:
                validate_rect_config_compatible(
                    ref_classes, task_config, other_task_id=tid
                )
            except ValueError as e:
                raise LabelUException(
                    code=ErrorCode.CODE_57026_YOLO_TRAIN_MERGE_INCOMPATIBLE,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    message=str(e),
                ) from e

        if frozen_samples_by_task is not None:
            rows = list(frozen_samples_by_task.get(int(tid), []))
        else:
            rows = (
                db.query(TaskSample)
                .filter(
                    TaskSample.task_id == tid,
                    TaskSample.state == SampleState.DONE.value,
                    TaskSample.deleted_at.is_(None),
                )
                .order_by(TaskSample.id.asc())
                .all()
            )
            rows = filter_trainable_samples(db, task=task, samples=rows)
        per_task_counts[tid] = len(rows)
        per_task_samples[tid] = rows
        all_samples.extend(rows)

    train_config = primary_config

    try:
        validate_merge_tasks_sample_labels(
            ref_classes,
            per_task_samples=per_task_samples,
            primary_task_id=primary_task_id,
            task_config=train_config,
        )
    except ValueError as e:
        raise LabelUException(
            code=ErrorCode.CODE_57026_YOLO_TRAIN_MERGE_INCOMPATIBLE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(e),
        ) from e

    if not all_samples:
        raise LabelUException(
            code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="所选任务均无 DONE 样本，无法训练。",
        )

    train_config = primary_config
    if preset_slug and preset_row:
        train_config = dict(primary_config)
        tools = list(train_config.get("tools") or [])
        rect_idx = next(
            (i for i, t in enumerate(tools) if t.get("tool") == "rectTool"), None
        )
        attrs = [
            {"key": v, "value": v}
            for v in ref_classes
        ]
        if rect_idx is None:
            tools.append({"tool": "rectTool", "config": {"attributes": attrs}})
        else:
            rcfg = dict(tools[rect_idx].get("config") or {})
            rcfg["attributes"] = attrs
            tools[rect_idx] = {**tools[rect_idx], "config": rcfg}
        train_config["tools"] = tools

    try:
        validate_yolo_training_prerequisites(train_config, all_samples)
    except ValueError as e:
        raise LabelUException(
            code=ErrorCode.CODE_57002_YOLO_TRAIN_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(e),
        ) from e

    return primary, train_config, all_samples, per_task_counts


def resolve_source_task_ids_for_create(
    db: Session,
    *,
    primary_task_id: int,
    cmd: CreateYoloTrainJobCommand,
    current_user: User,
) -> Tuple[List[int], dict]:
    ids = normalize_source_task_ids(primary_task_id, cmd.merge_task_ids)
    _, _, _, per_task = load_merged_training_context(
        db,
        primary_task_id=primary_task_id,
        source_task_ids=ids,
        current_user=current_user,
        rect_preset_slug=cmd.rect_preset_slug,
    )
    return ids, per_task
