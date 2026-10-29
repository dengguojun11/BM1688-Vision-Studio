"""Activate historical contracts whose complete sample scopes were backfilled.

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "h6i7j8k9l0m1"
down_revision = "g5h6i7j8k9l0"
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


def _activate_verified_historical_contracts(bind) -> int:
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
    activated = 0
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

        sample_rows = bind.execute(
            sa.select(sample.c.id, sample.c.state).where(
                sample.c.task_id == int(task_row["id"]),
                sample.c.deleted_at.is_(None),
            )
        ).all()
        done_sample_ids = [
            int(row.id) for row in sample_rows if str(row.state or "") == "DONE"
        ]
        if not _audit_proves_complete_done_set(
            task_row,
            all_sample_count=len(sample_rows),
            done_sample_count=len(done_sample_ids),
        ):
            continue

        complete_scope_count = bind.execute(
            sa.select(sa.func.count())
            .select_from(scope)
            .where(
                scope.c.sample_id.in_(done_sample_ids),
                scope.c.contract_id == int(contract_row["id"]),
                scope.c.completion_status == "complete",
            )
        ).scalar()
        if int(complete_scope_count or 0) != len(done_sample_ids):
            continue

        bind.execute(
            contract.update()
            .where(contract.c.id == int(contract_row["id"]))
            .values(status="active")
        )
        activated += 1
    return activated


def upgrade() -> None:
    _activate_verified_historical_contracts(op.get_bind())


def downgrade() -> None:
    return
