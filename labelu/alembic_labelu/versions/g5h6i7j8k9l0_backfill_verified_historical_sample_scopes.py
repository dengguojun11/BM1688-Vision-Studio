"""Backfill verified historical DONE samples into annotation scopes.

Revision ID: g5h6i7j8k9l0
Revises: f4g5h6i7j8k9
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "g5h6i7j8k9l0"
down_revision = "f4g5h6i7j8k9"
branch_labels = None
depends_on = None


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _annotation_sha256(value: dict) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rect_items(result: dict) -> list[dict]:
    block = result.get("rectTool") or result.get("rect") or {}
    items = block.get("result") if isinstance(block, dict) else block
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _scope_values(
    sample_row,
    *,
    contract_id: int,
    contract_label_ids: list[int],
    contract_label_values: set[str],
    now: datetime,
) -> dict | None:
    outer = _json_dict(sample_row.get("data"))
    result_raw = outer.get("result")
    if isinstance(result_raw, str):
        result = _json_dict(result_raw)
    else:
        result = result_raw if isinstance(result_raw, dict) else {}
    if not result:
        return None

    rects = _rect_items(result)
    explicit_negative = False
    if rects:
        rect_labels = [str(item.get("label") or "").strip() for item in rects]
        if any(
            not label or label not in contract_label_values for label in rect_labels
        ):
            return None
    else:
        explicit_negative = (
            str(outer.get("training_sample_type") or "").strip().lower() == "negative"
        )
        if not explicit_negative:
            return None

    reviewed_at = sample_row.get("updated_at") or now
    reviewed_by = sample_row.get("updated_by") or sample_row.get("created_by")
    return {
        "sample_id": int(sample_row["id"]),
        "contract_id": int(contract_id),
        "completion_status": "complete",
        "explicit_negative": bool(explicit_negative),
        "negative_label_ids_json": json.dumps(
            contract_label_ids if explicit_negative else []
        ),
        "annotation_sha256": _annotation_sha256(outer),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "created_at": now,
        "updated_at": now,
    }


def _audit_proves_complete_done_set(
    task_row,
    *,
    all_sample_count: int,
    done_sample_count: int,
) -> bool:
    if not bool(task_row.get("training_ready")):
        return False
    audit = _json_dict(task_row.get("annotation_audit_json"))
    if not bool(audit.get("training_ready")):
        return False
    try:
        total = int(audit.get("total_samples") or 0)
        passed = int(audit.get("passed_samples") or 0)
        failed = int(audit.get("failed_samples_count") or 0)
    except (TypeError, ValueError):
        return False
    return (
        total > 0
        and failed == 0
        and passed == total
        and total == int(all_sample_count)
        and total == int(done_sample_count)
    )


def _backfill_verified_historical_scopes(bind) -> int:
    tables = set(inspect(bind).get_table_names())
    required = {
        "task",
        "task_sample",
        "task_annotation_contract",
        "sample_annotation_scope",
    }
    if not required.issubset(tables):
        return 0

    metadata = sa.MetaData()
    task = sa.Table("task", metadata, autoload_with=bind)
    sample = sa.Table("task_sample", metadata, autoload_with=bind)
    contract = sa.Table("task_annotation_contract", metadata, autoload_with=bind)
    scope = sa.Table("sample_annotation_scope", metadata, autoload_with=bind)

    required_task_columns = {
        "id",
        "training_ready",
        "annotation_audit_json",
        "current_annotation_contract_id",
        "deleted_at",
    }
    required_sample_columns = {
        "id",
        "task_id",
        "state",
        "data",
        "created_by",
        "updated_by",
        "updated_at",
        "deleted_at",
    }
    if not required_task_columns.issubset(task.c.keys()):
        return 0
    if not required_sample_columns.issubset(sample.c.keys()):
        return 0

    task_rows = (
        bind.execute(
            sa.select(task).where(
                task.c.deleted_at.is_(None),
                task.c.training_ready == sa.true(),
                task.c.current_annotation_contract_id.is_not(None),
            )
        )
        .mappings()
        .all()
    )
    inserted = 0
    now = datetime.now()
    for task_row in task_rows:
        contract_row = (
            bind.execute(
                sa.select(contract).where(
                    contract.c.id == int(task_row["current_annotation_contract_id"]),
                    contract.c.task_id == int(task_row["id"]),
                    contract.c.revision == 1,
                    contract.c.status == "review_required",
                )
            )
            .mappings()
            .first()
        )
        if contract_row is None:
            continue

        label_ids = [
            int(value)
            for value in _json_list(contract_row.get("label_ids_json"))
            if str(value).strip().isdigit()
        ]
        label_values = {
            str(item.get("label_value") or "").strip()
            for item in _json_list(contract_row.get("labels_snapshot_json"))
            if isinstance(item, dict) and str(item.get("label_value") or "").strip()
        }
        if not label_ids or not label_values:
            continue

        sample_rows = (
            bind.execute(
                sa.select(sample).where(
                    sample.c.task_id == int(task_row["id"]),
                    sample.c.deleted_at.is_(None),
                )
            )
            .mappings()
            .all()
        )
        done_rows = [
            row for row in sample_rows if str(row.get("state") or "") == "DONE"
        ]
        if not _audit_proves_complete_done_set(
            task_row,
            all_sample_count=len(sample_rows),
            done_sample_count=len(done_rows),
        ):
            continue

        sample_ids = [int(row["id"]) for row in done_rows]
        existing_rows = bind.execute(
            sa.select(
                scope.c.sample_id,
                scope.c.contract_id,
                scope.c.completion_status,
            ).where(scope.c.sample_id.in_(sample_ids))
        ).all()
        if any(
            int(row.contract_id) != int(contract_row["id"]) for row in existing_rows
        ):
            continue
        existing_sample_ids = {int(row.sample_id) for row in existing_rows}
        pending = []
        for sample_row in done_rows:
            if int(sample_row["id"]) in existing_sample_ids:
                continue
            values = _scope_values(
                sample_row,
                contract_id=int(contract_row["id"]),
                contract_label_ids=label_ids,
                contract_label_values=label_values,
                now=now,
            )
            if values is not None:
                pending.append(values)
            if len(pending) >= 500:
                bind.execute(scope.insert(), pending)
                inserted += len(pending)
                pending = []
        if pending:
            bind.execute(scope.insert(), pending)
            inserted += len(pending)
    return inserted


def upgrade() -> None:
    _backfill_verified_historical_scopes(op.get_bind())


def downgrade() -> None:
    # Scope rows are evidence records. Keep them so downgrading does not erase
    # verification history or break already-frozen training manifests.
    return
