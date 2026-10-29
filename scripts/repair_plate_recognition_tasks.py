from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.plate_recognition_profile import (
    normalize_plate_recognition_sample_data,
    normalize_plate_recognition_task_config,
    task_config_has_plate_recognition_marker,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair selected historical plate-recognition tasks"
    )
    parser.add_argument(
        "--task-id",
        type=int,
        action="append",
        dest="task_ids",
        required=True,
        help="Task ID to repair; repeat for multiple tasks",
    )
    parser.add_argument("--apply", action="store_true", help="Persist changes")
    return parser.parse_args()


def _parse_json_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def repair_plate_recognition_tasks(
    db: Session,
    *,
    task_ids: list[int],
    apply_changes: bool,
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for task_id in sorted({int(value) for value in task_ids}):
        task = (
            db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
        )
        if task is None:
            summary.append({"task_id": task_id, "found": False})
            continue

        task_config = _parse_json_dict(task.config)
        if task_config is None:
            summary.append(
                {
                    "task_id": task_id,
                    "found": True,
                    "error": "invalid_task_config",
                }
            )
            continue
        if not task_config_has_plate_recognition_marker(task_config):
            summary.append(
                {
                    "task_id": task_id,
                    "found": True,
                    "error": "plate_marker_not_found",
                }
            )
            continue

        normalized_config = normalize_plate_recognition_task_config(task_config)
        config_changed = normalized_config != task_config
        stats = {
            "sample_scanned": 0,
            "sample_invalid_json": 0,
            "sample_changed": 0,
            "rect_labels": 0,
            "polygon_labels": 0,
            "attribute_keys": 0,
            "attribute_values": 0,
        }
        rows = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == task_id,
                TaskSample.deleted_at.is_(None),
            )
            .order_by(TaskSample.id.asc())
            .all()
        )
        for sample in rows:
            stats["sample_scanned"] += 1
            sample_data = _parse_json_dict(sample.data)
            if sample_data is None:
                stats["sample_invalid_json"] += 1
                continue
            normalized_data, sample_stats = normalize_plate_recognition_sample_data(
                sample_data
            )
            for key, value in sample_stats.items():
                stats[key] += value
            if normalized_data == sample_data:
                continue
            stats["sample_changed"] += 1
            if apply_changes:
                sample.data = json.dumps(normalized_data, ensure_ascii=False)
                sample.updated_at = datetime.now()
                db.add(sample)

        changed = config_changed or stats["sample_changed"] > 0
        if apply_changes and changed:
            if config_changed:
                task.config = json.dumps(normalized_config, ensure_ascii=False)
            task.training_ready = None
            task.annotation_audit_json = None
            task.updated_at = datetime.now()
            db.add(task)

        summary.append(
            {
                "task_id": task_id,
                "found": True,
                "config_changed": config_changed,
                **stats,
            }
        )
    return summary


def main() -> int:
    args = _parse_args()
    with session_scope() as db:
        with begin_transaction(db):
            summary = repair_plate_recognition_tasks(
                db,
                task_ids=args.task_ids,
                apply_changes=args.apply,
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
