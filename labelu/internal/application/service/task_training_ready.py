"""持久化 task.training_ready 并在不达标时将 FINISHED 任务降回 INPROGRESS。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.adapter.persistence import crud_sample, crud_task
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.config import settings
from labelu.internal.common.task_annotation_completeness import (
    audit_task_config_and_samples,
)
from labelu.internal.domain.models.annotation_contract import TaskAnnotationContract
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

_INCREMENTAL_AUDIT_COUNTERS = (
    "total_samples",
    "passed_samples",
    "failed_samples_count",
    "failed_missing_tags_count",
    "failed_missing_rect_count",
    "failed_missing_geometry_count",
    "failed_geometry_count",
    "failed_incomplete_count",
)


def _parse_audit_json(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def refresh_task_training_ready(
    db: Session,
    task: Task,
    *,
    allow_status_transition: bool = True,
) -> Dict[str, Any]:
    """
    重算并写回 task.training_ready / annotation_audit_json。
    图像任务质检失败且当前为 FINISHED 时，降为 INPROGRESS（训练不可选）。
    """
    if task.media_type != MediaType.IMAGE.value:
        obj_in = {
            Task.training_ready.key: None,
            Task.annotation_audit_json.key: None,
        }
        with begin_transaction(db):
            crud_task.update(db=db, db_obj=task, obj_in=obj_in)
        return {"training_ready": None, "skipped": True, "reason": "non_image_task"}

    samples = crud_sample.list_by_task(db=db, task_id=task.id)
    audit = audit_task_config_and_samples(task.config, samples)
    audit_json = json.dumps(audit, ensure_ascii=False)

    obj_in: Dict[str, Any] = {
        Task.training_ready.key: bool(audit.get("training_ready")),
        Task.annotation_audit_json.key: audit_json,
    }
    statics = crud_sample.statics(db=db, task_ids=[task.id])
    new_sample_cnt = statics.get(f"{task.id}_{SampleState.NEW.value}", 0)
    if allow_status_transition and (
        not audit.get("training_ready") and task.status == TaskStatus.FINISHED.value
    ):
        obj_in[Task.status.key] = TaskStatus.INPROGRESS.value
    elif allow_status_transition and (
        audit.get("training_ready")
        and task.status in {TaskStatus.INPROGRESS.value, TaskStatus.CONFIGURED.value}
        and new_sample_cnt == 0
    ):
        obj_in[Task.status.key] = TaskStatus.FINISHED.value

    with begin_transaction(db):
        crud_task.update(db=db, db_obj=task, obj_in=obj_in)

    _enqueue_training_ready_event(db, task)

    audit["training_ready"] = bool(audit.get("training_ready"))
    return audit


def refresh_task_training_ready_after_sample_change(
    db: Session,
    task: Task,
    *,
    previous_audit: Dict[str, Any],
    current_audit: Dict[str, Any],
    previous_state: str,
    current_state: str,
) -> Dict[str, Any]:
    """Update cached task audit from one sample, with full-audit fallback."""
    task_id = int(task.id)
    with begin_transaction(db):
        locked_task = crud_task.get(db=db, task_id=task_id, lock=True)
        cached = _parse_audit_json(
            locked_task.annotation_audit_json if locked_task is not None else None
        )
        if not _can_update_audit_incrementally(cached):
            cached = None
        else:
            audit = dict(cached)
            _apply_sample_audit_delta(
                audit,
                previous_audit=previous_audit,
                current_audit=current_audit,
                previous_state=previous_state,
                current_state=current_state,
            )
            blocking_total = int(audit["passed_samples"]) + int(
                audit["failed_samples_count"]
            )
            audit["training_ready"] = (
                blocking_total > 0 and int(audit["failed_samples_count"]) == 0
            )
            obj_in: Dict[str, Any] = {
                Task.training_ready.key: bool(audit["training_ready"]),
                Task.annotation_audit_json.key: json.dumps(audit, ensure_ascii=False),
            }
            if (
                not audit["training_ready"]
                and locked_task.status == TaskStatus.FINISHED.value
            ):
                obj_in[Task.status.key] = TaskStatus.INPROGRESS.value
            elif audit["training_ready"] and locked_task.status in {
                TaskStatus.INPROGRESS.value,
                TaskStatus.CONFIGURED.value,
            }:
                statics = crud_sample.statics(db=db, task_ids=[task_id])
                if statics.get(f"{task_id}_{SampleState.NEW.value}", 0) == 0:
                    obj_in[Task.status.key] = TaskStatus.FINISHED.value
            crud_task.update(db=db, db_obj=locked_task, obj_in=obj_in)

    if cached is None:
        refreshed_task = crud_task.get(db=db, task_id=task_id)
        return refresh_task_training_ready(db, refreshed_task)

    _enqueue_training_ready_event(db, locked_task)
    return audit


def _can_update_audit_incrementally(audit: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(audit, dict):
        return False
    # Historical audit snapshots predate the generic geometry counter.
    if "failed_missing_geometry_count" not in audit:
        audit["failed_missing_geometry_count"] = audit.get(
            "failed_missing_rect_count", 0
        )
    try:
        for key in _INCREMENTAL_AUDIT_COUNTERS:
            if int(audit[key]) < 0:
                return False
    except (KeyError, TypeError, ValueError):
        return False
    failed_samples = audit.get("failed_samples")
    if not isinstance(failed_samples, list):
        return False
    # ``failed_samples`` is a display cache capped at 50 rows.  The counters
    # above remain authoritative, so a capped list must not force a full audit
    # of every sample (large tasks would otherwise re-audit thousands of rows
    # on each annotation save).
    return len(failed_samples) == min(int(audit["failed_samples_count"]), 50)


def _apply_sample_audit_delta(
    audit: Dict[str, Any],
    *,
    previous_audit: Dict[str, Any],
    current_audit: Dict[str, Any],
    previous_state: str,
    current_state: str,
) -> None:
    def blocks_training(state: str) -> bool:
        return state not in (SampleState.NEW.value, SampleState.SKIPPED.value)

    previous_blocks = blocks_training(previous_state)
    current_blocks = blocks_training(current_state)
    previous_ok = bool(previous_audit.get("ok"))
    current_ok = bool(current_audit.get("ok"))
    audit["passed_samples"] += int(current_blocks and current_ok) - int(
        previous_blocks and previous_ok
    )
    audit["failed_samples_count"] += int(current_blocks and not current_ok) - int(
        previous_blocks and not previous_ok
    )
    counter_predicates = {
        "failed_missing_tags_count": lambda row: not row.get("tags_ok"),
        "failed_missing_rect_count": lambda row: (
            row.get("geometry_tool") in (None, "rectTool")
            and not row.get("geometry_annotation_ok", row.get("rect_ok"))
        ),
        "failed_missing_geometry_count": lambda row: not row.get(
            "geometry_annotation_ok", row.get("rect_ok")
        ),
        "failed_geometry_count": lambda row: not row.get("geometry_ok"),
        "failed_incomplete_count": lambda row: row.get("sample_quality_state")
        == SampleState.INCOMPLETE.value,
    }
    for key, predicate in counter_predicates.items():
        audit[key] += int(predicate(current_audit)) - int(predicate(previous_audit))

    sample_id = int(current_audit.get("sample_id") or previous_audit.get("sample_id"))
    failed_samples = [
        row
        for row in audit["failed_samples"]
        if int(row.get("sample_id") or 0) != sample_id
    ]
    if current_blocks and not current_ok:
        failed_samples.append(
            {
                "sample_id": sample_id,
                "filename": current_audit.get("filename"),
                "missing_tag_keys": current_audit.get("missing_tag_keys") or [],
                "geometry_annotation_ok": current_audit.get(
                    "geometry_annotation_ok", current_audit.get("rect_ok")
                ),
                "geometry_tool": current_audit.get("geometry_tool"),
                "rect_ok": current_audit.get("rect_ok"),
                "geometry_ok": current_audit.get("geometry_ok"),
                "geometry_error": current_audit.get("geometry_error"),
                "sample_quality_state": current_audit.get("sample_quality_state"),
            }
        )
    audit["failed_samples"] = failed_samples[:50]


def _enqueue_training_ready_event(db: Session, task: Task) -> None:
    if (
        not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
        or not bool(task.training_ready)
        or not task.dataset_category_id
        or not task.current_annotation_contract_id
        or db is None
    ):
        return
    contract = (
        db.query(TaskAnnotationContract)
        .filter(
            TaskAnnotationContract.id == task.current_annotation_contract_id,
            TaskAnnotationContract.task_id == task.id,
            TaskAnnotationContract.status.in_(("active", "approved")),
        )
        .first()
    )
    if contract is not None:
        enqueue_auto_training_event(
            db,
            event_name="task.training_ready.v1",
            aggregate_type="task",
            aggregate_id=str(task.id),
            payload={
                "task_id": int(task.id),
                "dataset_category_id": int(task.dataset_category_id),
                "label_set_signature": contract.label_set_signature,
                "class_order_signature": contract.class_order_signature,
            },
            idempotency_key=(
                f"task:{task.id}:training-ready:"
                f"{contract.label_set_signature}:{contract.class_order_signature}"
            ),
        )


def ensure_task_training_ready_refreshed(
    db: Session,
    task: Task,
    *,
    allow_status_transition: bool = True,
) -> Dict[str, Any]:
    """列表/详情：历史任务 training_ready 为空时补算一次。"""
    if task.media_type != MediaType.IMAGE.value:
        return _parse_audit_json(task.annotation_audit_json) or {"training_ready": None}
    if task.training_ready is not None and task.annotation_audit_json:
        cached = _parse_audit_json(task.annotation_audit_json)
        if cached is not None:
            cached["training_ready"] = bool(task.training_ready)
            return cached
    return refresh_task_training_ready(
        db,
        task,
        allow_status_transition=allow_status_transition,
    )


def training_ready_summary_for_response(task: Task) -> Dict[str, Any]:
    audit = _parse_audit_json(task.annotation_audit_json) or {}
    if task.media_type != MediaType.IMAGE.value:
        return {
            "training_ready": None,
            "annotation_audit": None,
        }
    tr = task.training_ready
    if tr is None and audit:
        tr = audit.get("training_ready")
    summary = {
        "training_ready": bool(tr) if tr is not None else False,
        "total_samples": audit.get("total_samples"),
        "passed_samples": audit.get("passed_samples"),
        "failed_samples_count": audit.get("failed_samples_count"),
        "failed_missing_tags_count": audit.get("failed_missing_tags_count"),
        "failed_missing_rect_count": audit.get("failed_missing_rect_count"),
        "failed_missing_geometry_count": audit.get("failed_missing_geometry_count"),
        "failed_incomplete_count": audit.get("failed_incomplete_count"),
        "failed_geometry_count": audit.get("failed_geometry_count"),
    }
    return {
        "training_ready": summary["training_ready"] if tr is not None else None,
        "annotation_audit": summary if audit else None,
    }
