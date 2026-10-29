"""Persist effective categories for expert-feedback imports and groups.

Revision ID: b9c0d1e2f3a4
Revises: s5t6u7v8w9x0
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b9c0d1e2f3a4"
down_revision = "s5t6u7v8w9x0"
branch_labels = None
depends_on = None


def _add_category_column(table_name: str) -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
    if "dataset_category_id" not in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("dataset_category_id", sa.Integer(), nullable=True))

    indexes = {item.get("name") for item in inspect(bind).get_indexes(table_name)}
    index_name = f"ix_{table_name}_dataset_category_id"
    if index_name not in indexes:
        op.create_index(index_name, table_name, ["dataset_category_id"], unique=False)

    foreign_keys = {item.get("name") for item in inspect(bind).get_foreign_keys(table_name)}
    foreign_key_name = {
        "experiment_dataset_import": "fk_exp_dataset_import_category",
        "experiment_dataset_group": "fk_exp_dataset_group_category",
    }[table_name]
    if foreign_key_name not in foreign_keys:
        with op.batch_alter_table(table_name) as batch:
            batch.create_foreign_key(
                foreign_key_name,
                "dataset_category",
                ["dataset_category_id"],
                ["id"],
            )


def upgrade() -> None:
    _add_category_column("experiment_dataset_import")
    _add_category_column("experiment_dataset_group")
    _backfill_effective_categories()


def _backfill_effective_categories() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    imports = sa.Table("experiment_dataset_import", metadata, autoload_with=bind)
    groups = sa.Table("experiment_dataset_group", metadata, autoload_with=bind)
    tasks = sa.Table("task", metadata, autoload_with=bind)
    exports = sa.Table("yolo_bm1688_export", metadata, autoload_with=bind)
    jobs = sa.Table("yolo_train_job", metadata, autoload_with=bind)

    category_by_model: dict[str, int] = {}
    export_rows = bind.execute(
        sa.select(exports.c.published_model_id, jobs.c.dataset_category_id)
        .select_from(exports.join(jobs, exports.c.yolo_train_job_id == jobs.c.id))
        .where(
            exports.c.published_model_id.is_not(None),
            jobs.c.dataset_category_id.is_not(None),
        )
        .order_by(exports.c.id.asc())
    )
    for row in export_rows:
        category_by_model[str(row.published_model_id)] = int(row.dataset_category_id)

    task_category_by_id: dict[int, int] = {}
    import_rows = list(
        bind.execute(
            sa.select(
                imports.c.id,
                imports.c.model_id,
                imports.c.aggregation_group_id,
                imports.c.formal_task_ids_json,
                imports.c.draft_task_id,
                imports.c.dataset_category_id,
            ).where(imports.c.deleted_at.is_(None))
        ).mappings()
    )
    group_category_by_id: dict[int, int] = {}
    for row in import_rows:
        category_id = row["dataset_category_id"]
        if category_id is None:
            category_id = category_by_model.get(str(row["model_id"] or "").strip())
            if category_id is not None:
                bind.execute(
                    imports.update()
                    .where(imports.c.id == int(row["id"]))
                    .where(imports.c.dataset_category_id.is_(None))
                    .values(dataset_category_id=int(category_id))
                )
        if category_id is None:
            continue
        group_id = int(row["aggregation_group_id"] or 0)
        if group_id and group_id not in group_category_by_id:
            group_category_by_id[group_id] = int(category_id)
        task_ids: list[int] = []
        try:
            parsed = json.loads(row["formal_task_ids_json"] or "[]")
            if isinstance(parsed, list):
                task_ids.extend(int(value) for value in parsed if int(value) > 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if row["draft_task_id"]:
            task_ids.append(int(row["draft_task_id"]))
        for task_id in task_ids:
            task_category_by_id.setdefault(task_id, int(category_id))

    for group_id, category_id in group_category_by_id.items():
        bind.execute(
            groups.update()
            .where(groups.c.id == int(group_id))
            .where(groups.c.dataset_category_id.is_(None))
            .values(dataset_category_id=int(category_id))
        )

    for row in bind.execute(
        sa.select(groups.c.id, groups.c.formal_task_id, groups.c.dataset_category_id)
    ).mappings():
        category_id = row["dataset_category_id"]
        if category_id is None and row["formal_task_id"]:
            category_id = task_category_by_id.get(int(row["formal_task_id"]))
            if category_id is not None:
                bind.execute(
                    groups.update()
                    .where(groups.c.id == int(row["id"]))
                    .where(groups.c.dataset_category_id.is_(None))
                    .values(dataset_category_id=int(category_id))
                )
        if category_id is not None:
            bind.execute(
                imports.update()
                .where(imports.c.aggregation_group_id == int(row["id"]))
                .where(imports.c.dataset_category_id.is_(None))
                .values(dataset_category_id=int(category_id))
            )

    for task_id, category_id in task_category_by_id.items():
        bind.execute(
            tasks.update()
            .where(tasks.c.id == int(task_id))
            .where(tasks.c.dataset_category_id.is_(None))
            .values(dataset_category_id=int(category_id))
        )


def _drop_category_column(table_name: str) -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
    if "dataset_category_id" not in columns:
        return
    indexes = {item.get("name") for item in inspect(bind).get_indexes(table_name)}
    foreign_keys = {item.get("name") for item in inspect(bind).get_foreign_keys(table_name)}
    index_name = f"ix_{table_name}_dataset_category_id"
    foreign_key_name = {
        "experiment_dataset_import": "fk_exp_dataset_import_category",
        "experiment_dataset_group": "fk_exp_dataset_group_category",
    }[table_name]
    with op.batch_alter_table(table_name) as batch:
        if foreign_key_name in foreign_keys:
            batch.drop_constraint(foreign_key_name, type_="foreignkey")
        if index_name in indexes:
            batch.drop_index(index_name)
        batch.drop_column("dataset_category_id")


def downgrade() -> None:
    _drop_category_column("experiment_dataset_group")
    _drop_category_column("experiment_dataset_import")
