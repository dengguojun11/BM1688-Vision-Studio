from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.training_task import TrainingTask


def _dump_json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def create(
    db: Session,
    *,
    owner_id: int,
    name: str,
    mode: str,
    status: str,
    primary_task_id: Optional[int],
    selected_presets_snapshot: Any,
    selected_label_items_snapshot: Any,
    merged_class_names_snapshot: Any,
    source_tasks_snapshot: Any,
    ignored_tasks_snapshot: Any,
    preview_summary: Any | None = None,
    latest_job_id: int | None = None,
    latest_job_status: str | None = None,
    series_id: int | None = None,
    variant_id: int | None = None,
    branch_key: str | None = None,
    selection_contract_sha256: str | None = None,
    selection_contract_json: str | None = None,
    sample_conditions_json: str | None = None,
) -> TrainingTask:
    row = TrainingTask(
        owner_id=owner_id,
        name=name,
        mode=mode,
        status=status,
        primary_task_id=primary_task_id,
        selected_presets_snapshot_json=_dump_json(selected_presets_snapshot),
        selected_label_items_snapshot_json=_dump_json(selected_label_items_snapshot),
        merged_class_names_snapshot_json=_dump_json(merged_class_names_snapshot),
        source_tasks_snapshot_json=_dump_json(source_tasks_snapshot),
        ignored_tasks_snapshot_json=_dump_json(ignored_tasks_snapshot),
        preview_summary_json=(
            json.dumps(preview_summary, ensure_ascii=False)
            if preview_summary is not None
            else None
        ),
        latest_job_id=latest_job_id,
        latest_job_status=latest_job_status,
        series_id=series_id,
        variant_id=variant_id,
        branch_key=(branch_key or "").strip() or None,
        selection_contract_sha256=(selection_contract_sha256 or "").strip() or None,
        selection_contract_json=selection_contract_json,
        sample_conditions_json=sample_conditions_json,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, training_task_id: int) -> TrainingTask | None:
    return (
        db.query(TrainingTask)
        .filter(TrainingTask.id == training_task_id)
        .first()
    )


def list_by_owner(
    db: Session,
    owner_id: int,
    *,
    page: int = 0,
    size: int = 20,
    name: str | None = None,
    search: str | None = None,
    status: str | None = None,
) -> tuple[list[TrainingTask], int]:
    q = db.query(TrainingTask).filter(TrainingTask.owner_id == owner_id)
    if name and name.strip():
        q = q.filter(
            TrainingTask.name.ilike(literal_like_pattern(name), escape=LIKE_ESCAPE)
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        q = q.filter(
            or_(
                cast(TrainingTask.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.name.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.mode.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(TrainingTask.primary_task_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(TrainingTask.latest_job_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingTask.latest_job_status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(TrainingTask.dataset_category_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingTask.training_recipe_key.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingTask.runtime_profile.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.source_mode.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingTask.source_tasks_snapshot_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    if status and status.strip():
        q = q.filter(TrainingTask.status == status.strip())
    total = q.count()
    rows = (
        q.order_by(TrainingTask.id.desc())
        .offset(max(0, page) * size)
        .limit(size)
        .all()
    )
    return rows, total


def name_exists(db: Session, owner_id: int, name: str) -> bool:
    return (
        db.query(TrainingTask)
        .filter(
            TrainingTask.owner_id == owner_id,
            TrainingTask.name == name,
        )
        .first()
        is not None
    )


def update(
    db: Session,
    row: TrainingTask,
    *,
    status: str | None = None,
    latest_job_id: int | None = None,
    latest_job_status: str | None = None,
    preview_summary: Any | None = None,
) -> TrainingTask:
    if status is not None:
        row.status = status
    if latest_job_id is not None:
        row.latest_job_id = latest_job_id
    if latest_job_status is not None:
        row.latest_job_status = latest_job_status
    if preview_summary is not None:
        row.preview_summary_json = json.dumps(preview_summary, ensure_ascii=False)
    row.updated_at = datetime.now()
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def parse_snapshot(raw: str | None) -> Any:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
