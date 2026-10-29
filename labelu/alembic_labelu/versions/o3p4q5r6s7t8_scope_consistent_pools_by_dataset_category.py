"""Scope consistent pools by their source dataset category.

Revision ID: o3p4q5r6s7t8
Revises: h6i7j8k9l0m1
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "o3p4q5r6s7t8"
down_revision = "h6i7j8k9l0m1"
branch_labels = None
depends_on = None


REGISTRY_TABLE = "yolo_consistent_pool_registry"
SYSTEM_CATEGORY_KEY = "training_consistent_pool"
OLD_UNIQUE = "uq_yolo_consistent_pool_registry_signature"
NEW_UNIQUE = "uq_yolo_consistent_pool_registry_scope_signature"


def _scope_key(category_id: int | None) -> str:
    return (
        f"category:{int(category_id)}" if category_id is not None else "uncategorized"
    )


def _pool_signature(task_row) -> str:
    try:
        config = json.loads(task_row.get("config") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        config = {}
    marker = config.get("consistent_pool") if isinstance(config, dict) else None
    marker = marker if isinstance(marker, dict) else {}
    return str(
        marker.get("label_set_signature")
        or task_row.get("source_external_task_id")
        or ""
    ).strip()


def _source_category_id(bind, task, sample, pool_task_id: int, system_category_id):
    source_task_ids: set[int] = set()
    rows = bind.execute(
        sa.select(sample.c.data).where(
            sample.c.task_id == int(pool_task_id),
            sample.c.deleted_at.is_(None),
        )
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[0] or "{}")
            meta = (
                payload.get("_consistent_pool") if isinstance(payload, dict) else None
            )
            source_task_id = (
                meta.get("source_task_id") if isinstance(meta, dict) else None
            )
            if source_task_id is not None:
                source_task_ids.add(int(source_task_id))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    if source_task_ids:
        source_rows = bind.execute(
            sa.select(task.c.id, task.c.dataset_category_id).where(
                task.c.id.in_(source_task_ids),
                task.c.deleted_at.is_(None),
            )
        ).fetchall()
        if len(source_rows) != len(source_task_ids):
            return None
        category_ids = {
            (
                int(row[1])
                if row[1] is not None and int(row[1]) != int(system_category_id or -1)
                else None
            )
            for row in source_rows
        }
        if len(category_ids) == 1:
            return next(iter(category_ids))
        return None
    current = bind.execute(
        sa.select(task.c.dataset_category_id).where(task.c.id == int(pool_task_id))
    ).scalar()
    if current is not None and int(current) != int(system_category_id or -1):
        return int(current)
    return None


def _update_pool_config(raw, *, category_id, category_key, category_name):
    try:
        config = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    marker = config.get("consistent_pool")
    marker = dict(marker) if isinstance(marker, dict) else {}
    marker["dataset_category_scope"] = _scope_key(category_id)
    marker["category"] = category_name or "未分类"
    if category_id is None:
        marker.pop("dataset_category_id", None)
        marker.pop("dataset_category_key", None)
    else:
        marker["dataset_category_id"] = int(category_id)
        marker["dataset_category_key"] = category_key or ""
    config["consistent_pool"] = marker
    return json.dumps(config, ensure_ascii=False)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if REGISTRY_TABLE not in tables:
        return

    columns = {row["name"] for row in inspect(bind).get_columns(REGISTRY_TABLE)}
    with op.batch_alter_table(REGISTRY_TABLE) as batch:
        if "category_scope_key" not in columns:
            batch.add_column(
                sa.Column("category_scope_key", sa.String(64), nullable=True)
            )
        if "dataset_category_id" not in columns:
            batch.add_column(
                sa.Column("dataset_category_id", sa.Integer(), nullable=True)
            )

    unique_names = {
        row.get("name")
        for row in inspect(bind).get_unique_constraints(REGISTRY_TABLE)
        if row.get("name")
    }
    with op.batch_alter_table(REGISTRY_TABLE) as batch:
        if OLD_UNIQUE in unique_names:
            batch.drop_constraint(OLD_UNIQUE, type_="unique")

    metadata = sa.MetaData()
    registry = sa.Table(REGISTRY_TABLE, metadata, autoload_with=bind)
    task = sa.Table("task", metadata, autoload_with=bind) if "task" in tables else None
    sample = (
        sa.Table("task_sample", metadata, autoload_with=bind)
        if "task_sample" in tables
        else None
    )
    category = (
        sa.Table("dataset_category", metadata, autoload_with=bind)
        if "dataset_category" in tables
        else None
    )

    system_category_id = None
    categories = {}
    if category is not None:
        system_category_id = bind.execute(
            sa.select(category.c.id).where(
                category.c.category_key == SYSTEM_CATEGORY_KEY
            )
        ).scalar()
        categories = {
            int(row[0]): (str(row[1] or ""), str(row[2] or ""))
            for row in bind.execute(
                sa.select(category.c.id, category.c.category_key, category.c.name)
            ).fetchall()
        }

    pool_tasks = []
    if task is not None:
        pool_tasks = (
            bind.execute(
                sa.select(task)
                .where(
                    task.c.deleted_at.is_(None),
                    sa.or_(
                        task.c.source_type == "consistent_pool",
                        task.c.config.like("%train_filtered_consistent%"),
                    ),
                )
                .order_by(task.c.id.asc())
            )
            .mappings()
            .all()
        )

    category_by_task: dict[int, int | None] = {}
    for task_row in pool_tasks:
        task_id = int(task_row["id"])
        category_id = (
            _source_category_id(bind, task, sample, task_id, system_category_id)
            if sample is not None
            else None
        )
        category_key, category_name = categories.get(category_id, ("", ""))
        category_by_task[task_id] = category_id
        bind.execute(
            task.update()
            .where(task.c.id == task_id)
            .values(
                dataset_category_id=category_id,
                config=_update_pool_config(
                    task_row.get("config"),
                    category_id=category_id,
                    category_key=category_key,
                    category_name=category_name,
                ),
            )
        )

    registry_rows = bind.execute(sa.select(registry)).mappings().all()
    registry_task_ids: set[int] = set()
    for row in registry_rows:
        task_id = int(row["task_id"]) if row.get("task_id") is not None else None
        category_id = category_by_task.get(task_id) if task_id is not None else None
        bind.execute(
            registry.update()
            .where(registry.c.id == int(row["id"]))
            .values(
                category_scope_key=_scope_key(category_id),
                dataset_category_id=category_id,
            )
        )
        if task_id is not None:
            registry_task_ids.add(task_id)

    for task_row in pool_tasks:
        task_id = int(task_row["id"])
        if task_id in registry_task_ids:
            continue
        signature = _pool_signature(task_row)
        if not signature:
            continue
        category_id = category_by_task.get(task_id)
        existing = bind.execute(
            sa.select(registry.c.id).where(
                registry.c.category_scope_key == _scope_key(category_id),
                registry.c.label_set_signature == signature,
            )
        ).scalar()
        if existing is None:
            bind.execute(
                registry.insert().values(
                    category_scope_key=_scope_key(category_id),
                    dataset_category_id=category_id,
                    label_set_signature=signature,
                    task_id=task_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    with op.batch_alter_table(REGISTRY_TABLE) as batch:
        batch.alter_column(
            "category_scope_key",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.create_unique_constraint(
            NEW_UNIQUE, ["category_scope_key", "label_set_signature"]
        )
        if category is not None:
            batch.create_foreign_key(
                "fk_consistent_pool_registry_dataset_category",
                "dataset_category",
                ["dataset_category_id"],
                ["id"],
            )
        batch.create_index(
            "ix_consistent_pool_registry_category_scope_key",
            ["category_scope_key"],
            unique=False,
        )
        batch.create_index(
            "ix_consistent_pool_registry_dataset_category_id",
            ["dataset_category_id"],
            unique=False,
        )

    if category is not None and system_category_id is not None:
        bind.execute(
            category.update()
            .where(category.c.id == int(system_category_id))
            .values(
                status="disabled",
                deleted_at=datetime.now(timezone.utc),
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if REGISTRY_TABLE not in tables:
        return
    metadata = sa.MetaData()
    registry = sa.Table(REGISTRY_TABLE, metadata, autoload_with=bind)
    task = sa.Table("task", metadata, autoload_with=bind) if "task" in tables else None
    category = (
        sa.Table("dataset_category", metadata, autoload_with=bind)
        if "dataset_category" in tables
        else None
    )
    system_category_id = None
    if category is not None:
        system_category_id = bind.execute(
            sa.select(category.c.id).where(
                category.c.category_key == SYSTEM_CATEGORY_KEY
            )
        ).scalar()
        if system_category_id is not None:
            bind.execute(
                category.update()
                .where(category.c.id == int(system_category_id))
                .values(status="active", deleted_at=None, updated_at=sa.func.now())
            )
    if task is not None and system_category_id is not None:
        bind.execute(
            task.update()
            .where(
                task.c.deleted_at.is_(None),
                sa.or_(
                    task.c.source_type == "consistent_pool",
                    task.c.config.like("%train_filtered_consistent%"),
                ),
            )
            .values(dataset_category_id=int(system_category_id))
        )

    duplicate_ids = []
    seen = set()
    for row in bind.execute(
        sa.select(registry.c.id, registry.c.label_set_signature).order_by(registry.c.id)
    ).fetchall():
        if row[1] in seen:
            duplicate_ids.append(int(row[0]))
        else:
            seen.add(row[1])
    if duplicate_ids:
        bind.execute(registry.delete().where(registry.c.id.in_(duplicate_ids)))

    indexes = {row.get("name") for row in inspect(bind).get_indexes(REGISTRY_TABLE)}
    foreign_keys = {
        row.get("name") for row in inspect(bind).get_foreign_keys(REGISTRY_TABLE)
    }
    unique_names = {
        row.get("name") for row in inspect(bind).get_unique_constraints(REGISTRY_TABLE)
    }
    with op.batch_alter_table(REGISTRY_TABLE) as batch:
        if "ix_consistent_pool_registry_category_scope_key" in indexes:
            batch.drop_index("ix_consistent_pool_registry_category_scope_key")
        if "ix_consistent_pool_registry_dataset_category_id" in indexes:
            batch.drop_index("ix_consistent_pool_registry_dataset_category_id")
        if "fk_consistent_pool_registry_dataset_category" in foreign_keys:
            batch.drop_constraint(
                "fk_consistent_pool_registry_dataset_category", type_="foreignkey"
            )
        if NEW_UNIQUE in unique_names:
            batch.drop_constraint(NEW_UNIQUE, type_="unique")
        batch.create_unique_constraint(OLD_UNIQUE, ["label_set_signature"])
        batch.drop_column("dataset_category_id")
        batch.drop_column("category_scope_key")
