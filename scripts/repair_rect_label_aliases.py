from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.application.service.rect_label_normalization import (
    normalize_sample_api_data_rect_labels,
    normalize_uploaded_pre_annotation_rect_labels,
)
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample


@dataclass
class RepairStats:
    scanned_samples: int = 0
    patched_samples: int = 0
    scanned_pre_annotations: int = 0
    patched_pre_annotations: int = 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair historical rect label aliases")
    parser.add_argument("--task-id", type=int, action="append", dest="task_ids")
    parser.add_argument("--apply", action="store_true", help="Persist changes")
    return parser.parse_args()


def _parse_json_dict(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _task_ids(db: Session, selected: list[int] | None) -> list[int]:
    if selected:
        return sorted({int(item) for item in selected if item})
    rows = (
        db.query(TaskSample.task_id)
        .filter(TaskSample.deleted_at.is_(None))
        .distinct()
        .all()
    )
    return sorted({int(row[0]) for row in rows if row and row[0]})


def _repair_task_samples(
    db: Session,
    *,
    task_id: int,
    task_config: dict[str, Any],
    apply_fix: bool,
) -> RepairStats:
    stats = RepairStats()
    rows = (
        db.query(TaskSample)
        .filter(
            TaskSample.task_id == task_id,
            TaskSample.deleted_at.is_(None),
        )
        .all()
    )
    for row in rows:
        stats.scanned_samples += 1
        data = _parse_json_dict(row.data)
        if not data:
            continue
        normalized, replaced, unknown, _allowed = normalize_sample_api_data_rect_labels(
            data,
            task_config=task_config,
        )
        if unknown or not replaced:
            continue
        stats.patched_samples += 1
        if apply_fix:
            row.data = json.dumps(normalized, ensure_ascii=False)
            row.updated_at = datetime.now()
            db.add(row)
    return stats


def _repair_task_pre_annotations(
    db: Session,
    *,
    task_id: int,
    task_config: dict[str, Any],
    apply_fix: bool,
) -> RepairStats:
    stats = RepairStats()
    rows = (
        db.query(TaskPreAnnotation)
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.deleted_at.is_(None),
        )
        .all()
    )
    for row in rows:
        stats.scanned_pre_annotations += 1
        data = _parse_json_dict(row.data)
        if not data:
            continue
        normalized, replaced, unknown, _allowed = (
            normalize_uploaded_pre_annotation_rect_labels(
                data,
                task_config=task_config,
            )
        )
        if unknown or not replaced:
            continue
        stats.patched_pre_annotations += 1
        if apply_fix:
            row.data = json.dumps(normalized, ensure_ascii=False)
            row.updated_at = datetime.now()
            db.add(row)
    return stats


def main() -> int:
    args = _parse_args()
    summary: list[dict[str, Any]] = []
    with session_scope() as db:
        task_ids = _task_ids(db, args.task_ids)
        with begin_transaction(db):
            for task_id in task_ids:
                task = crud_task.get(db=db, task_id=task_id)
                if not task:
                    continue
                task_config = _parse_json_dict(task.config) or {}
                sample_stats = _repair_task_samples(
                    db,
                    task_id=task_id,
                    task_config=task_config,
                    apply_fix=args.apply,
                )
                pre_stats = _repair_task_pre_annotations(
                    db,
                    task_id=task_id,
                    task_config=task_config,
                    apply_fix=args.apply,
                )
                summary.append(
                    {
                        "task_id": task_id,
                        "sample_scanned": sample_stats.scanned_samples,
                        "sample_patched": sample_stats.patched_samples,
                        "pre_annotation_scanned": pre_stats.scanned_pre_annotations,
                        "pre_annotation_patched": pre_stats.patched_pre_annotations,
                    }
                )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
