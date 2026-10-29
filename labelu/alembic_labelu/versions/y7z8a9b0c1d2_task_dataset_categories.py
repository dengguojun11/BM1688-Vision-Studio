"""Attach dataset categories to annotation tasks and backfill clear legacy links.

Revision ID: y7z8a9b0c1d2
Revises: x6y7z8a9b0c1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "y7z8a9b0c1d2"
down_revision = "x6y7z8a9b0c1"
branch_labels = None
depends_on = None

_KNOWN_DATASET_SUFFIXES = (
    "-训练筛选一致",
    "-train",
)


def _normalized_category_hint(value: object) -> str:
    normalized = str(value or "").strip().lower()
    for suffix in _KNOWN_DATASET_SUFFIXES:
        if normalized.endswith(suffix.lower()):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _category_lookup(bind) -> tuple[sa.Table, dict[str, int]]:
    metadata = sa.MetaData()
    categories = sa.Table("dataset_category", metadata, autoload_with=bind)
    lookup: dict[str, int] = {}
    for row in bind.execute(
        sa.select(
            categories.c.id,
            categories.c.category_key,
            categories.c.name,
        ).where(categories.c.deleted_at.is_(None))
    ).mappings():
        category_id = int(row["id"])
        for value in (row.get("category_key"), row.get("name")):
            hint = _normalized_category_hint(value)
            if hint:
                lookup[hint] = category_id
    return categories, lookup


def _unique_category_by_reference(rows, key_name: str, category_name: str) -> dict[int, int]:
    grouped: dict[int, set[int]] = {}
    for row in rows:
        key = row.get(key_name)
        category_id = row.get(category_name)
        if key is None or category_id is None:
            continue
        grouped.setdefault(int(key), set()).add(int(category_id))
    return {
        key: next(iter(category_ids))
        for key, category_ids in grouped.items()
        if len(category_ids) == 1
    }


def _backfill_task_categories(bind) -> None:
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "task" not in tables or "dataset_category" not in tables:
        return

    metadata = sa.MetaData()
    task = sa.Table("task", metadata, autoload_with=bind)
    _, category_by_hint = _category_lookup(bind)

    integration_categories: dict[int, int] = {}
    if "strategy_task_integration" in tables:
        integrations = sa.Table(
            "strategy_task_integration", metadata, autoload_with=bind
        )
        integration_categories = _unique_category_by_reference(
            bind.execute(
                sa.select(
                    integrations.c.labelu_task_id,
                    integrations.c.dataset_category_id,
                ).where(integrations.c.deleted_at.is_(None))
            ).mappings(),
            "labelu_task_id",
            "dataset_category_id",
        )

    job_categories: dict[int, int] = {}
    jobs = None
    if "yolo_train_job" in tables:
        jobs = sa.Table("yolo_train_job", metadata, autoload_with=bind)
        for row in bind.execute(
            sa.select(jobs.c.id, jobs.c.category, jobs.c.dataset_category_id)
        ).mappings():
            if row.get("dataset_category_id") is not None:
                continue
            category_id = category_by_hint.get(
                _normalized_category_hint(row.get("category"))
            )
            if category_id is not None:
                bind.execute(
                    jobs.update()
                    .where(jobs.c.id == int(row["id"]))
                    .values(dataset_category_id=category_id)
                )
        job_categories = _unique_category_by_reference(
            bind.execute(
                sa.select(jobs.c.task_id, jobs.c.dataset_category_id)
            ).mappings(),
            "task_id",
            "dataset_category_id",
        )

    for row in bind.execute(
        sa.select(task.c.id, task.c.name, task.c.dataset_category_id)
    ).mappings():
        if row.get("dataset_category_id") is not None:
            continue
        task_id = int(row["id"])
        category_id = integration_categories.get(task_id) or job_categories.get(task_id)
        if category_id is None:
            category_id = category_by_hint.get(
                _normalized_category_hint(row.get("name"))
            )
        if category_id is not None:
            bind.execute(
                task.update()
                .where(task.c.id == task_id)
                .values(dataset_category_id=category_id)
            )

    if "training_task" in tables:
        training_tasks = sa.Table("training_task", metadata, autoload_with=bind)
        training_job_categories = (
            _unique_category_by_reference(
                bind.execute(
                    sa.select(jobs.c.training_task_id, jobs.c.dataset_category_id)
                ).mappings(),
                "training_task_id",
                "dataset_category_id",
            )
            if jobs is not None
            else {}
        )
        task_categories = {
            int(row.id): int(row.dataset_category_id)
            for row in bind.execute(
                sa.select(task.c.id, task.c.dataset_category_id).where(
                    task.c.dataset_category_id.is_not(None)
                )
            )
        }
        for row in bind.execute(
            sa.select(
                training_tasks.c.id,
                training_tasks.c.primary_task_id,
                training_tasks.c.dataset_category_id,
            )
        ).mappings():
            if row.get("dataset_category_id") is not None:
                continue
            category_id = training_job_categories.get(int(row["id"]))
            if category_id is None and row.get("primary_task_id") is not None:
                category_id = task_categories.get(int(row["primary_task_id"]))
            if category_id is not None:
                bind.execute(
                    training_tasks.update()
                    .where(training_tasks.c.id == int(row["id"]))
                    .values(dataset_category_id=category_id)
                )

    if "strategy_task_integration" in tables:
        task_categories = {
            int(row.id): int(row.dataset_category_id)
            for row in bind.execute(
                sa.select(task.c.id, task.c.dataset_category_id).where(
                    task.c.dataset_category_id.is_not(None)
                )
            )
        }
        integrations = sa.Table(
            "strategy_task_integration", metadata, autoload_with=bind
        )
        for row in bind.execute(
            sa.select(
                integrations.c.id,
                integrations.c.labelu_task_id,
                integrations.c.dataset_category_id,
            )
        ).mappings():
            if row.get("dataset_category_id") is not None:
                continue
            task_id = row.get("labelu_task_id")
            category_id = task_categories.get(int(task_id)) if task_id else None
            if category_id is not None:
                bind.execute(
                    integrations.update()
                    .where(integrations.c.id == int(row["id"]))
                    .values(dataset_category_id=category_id)
                )


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("task")}
    if "dataset_category_id" not in columns:
        with op.batch_alter_table("task") as batch:
            batch.add_column(sa.Column("dataset_category_id", sa.Integer(), nullable=True))

    _backfill_task_categories(bind)

    inspector = inspect(bind)
    indexes = {item.get("name") for item in inspector.get_indexes("task")}
    if "ix_task_dataset_category_id" not in indexes:
        op.create_index(
            "ix_task_dataset_category_id",
            "task",
            ["dataset_category_id"],
        )
    foreign_keys = {item.get("name") for item in inspect(bind).get_foreign_keys("task")}
    if "fk_task_dataset_category_id_dataset_category" not in foreign_keys:
        with op.batch_alter_table("task") as batch:
            batch.create_foreign_key(
                "fk_task_dataset_category_id_dataset_category",
                "dataset_category",
                ["dataset_category_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("task")}
    if "dataset_category_id" not in columns:
        return
    foreign_keys = {item.get("name") for item in inspect(bind).get_foreign_keys("task")}
    indexes = {item.get("name") for item in inspect(bind).get_indexes("task")}
    with op.batch_alter_table("task") as batch:
        if "fk_task_dataset_category_id_dataset_category" in foreign_keys:
            batch.drop_constraint(
                "fk_task_dataset_category_id_dataset_category",
                type_="foreignkey",
            )
        if "ix_task_dataset_category_id" in indexes:
            batch.drop_index("ix_task_dataset_category_id")
        batch.drop_column("dataset_category_id")
