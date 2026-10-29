"""Add database-level consistent-pool registry.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


TABLE_NAME = "yolo_consistent_pool_registry"


def _pool_signature(source_type, source_external_task_id, config_raw) -> str:
    if str(source_type or "").strip() == "consistent_pool":
        signature = str(source_external_task_id or "").strip()
        if signature:
            return signature
    try:
        config = json.loads(config_raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    marker = config.get("consistent_pool") if isinstance(config, dict) else None
    if not isinstance(marker, dict) or marker.get("kind") != "train_filtered_consistent":
        return ""
    return str(marker.get("label_set_signature") or "").strip()


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if TABLE_NAME not in tables:
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("label_set_signature", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.UniqueConstraint(
                "label_set_signature", name="uq_yolo_consistent_pool_registry_signature"
            ),
            sa.UniqueConstraint("task_id", name="uq_yolo_consistent_pool_registry_task"),
        )

    if "task" not in tables:
        return
    task_table = sa.table(
        "task",
        sa.column("id", sa.Integer()),
        sa.column("source_type", sa.String()),
        sa.column("source_external_task_id", sa.String()),
        sa.column("config", sa.Text()),
        sa.column("deleted_at", sa.DateTime()),
    )
    registry_table = sa.table(
        TABLE_NAME,
        sa.column("label_set_signature", sa.String()),
        sa.column("task_id", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    seen = {
        str(row[0])
        for row in bind.execute(
            sa.select(registry_table.c.label_set_signature)
        ).fetchall()
        if row[0]
    }
    rows = bind.execute(
        sa.select(
            task_table.c.id,
            task_table.c.source_type,
            task_table.c.source_external_task_id,
            task_table.c.config,
        )
        .where(
            task_table.c.deleted_at.is_(None),
            sa.or_(
                task_table.c.source_type == "consistent_pool",
                task_table.c.config.like("%train_filtered_consistent%"),
            ),
        )
        .order_by(task_table.c.id.asc())
    ).fetchall()
    for row in rows:
        signature = _pool_signature(row[1], row[2], row[3])
        if not signature or signature in seen:
            continue
        bind.execute(
            registry_table.insert().values(
                label_set_signature=signature,
                task_id=int(row[0]),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        seen.add(signature)


def downgrade() -> None:
    if TABLE_NAME in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE_NAME)
