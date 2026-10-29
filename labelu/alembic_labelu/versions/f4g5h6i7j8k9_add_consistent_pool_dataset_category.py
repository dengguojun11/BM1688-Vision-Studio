"""Create the system category used by administrator-only consistent pools.

Revision ID: f4g5h6i7j8k9
Revises: e3f4a5b6c7d8
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f4g5h6i7j8k9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


CATEGORY_KEY = "training_consistent_pool"
CATEGORY_NAME = "训练一致数据集"
CATEGORY_DESCRIPTION = (
    "系统自动汇总人工标注与训练模型检测结果严格一致的样本，仅管理员可见和使用。"
)
CATEGORY_SORT_ORDER = 9000


def _ensure_category(bind) -> int | None:
    if "dataset_category" not in set(inspect(bind).get_table_names()):
        return None
    metadata = sa.MetaData()
    category = sa.Table("dataset_category", metadata, autoload_with=bind)
    row = bind.execute(
        sa.select(category).where(category.c.category_key == CATEGORY_KEY)
    ).mappings().first()
    values = {
        "name": CATEGORY_NAME,
        "description": CATEGORY_DESCRIPTION,
        "sort_order": CATEGORY_SORT_ORDER,
        "status": "active",
        "deleted_at": None,
        "updated_at": sa.func.now(),
    }
    if row is not None:
        changed = any(row.get(key) != value for key, value in values.items() if key != "updated_at")
        if changed:
            values["version"] = int(row.get("version") or 0) + 1
            bind.execute(
                category.update()
                .where(category.c.id == int(row["id"]))
                .values(**values)
            )
        return int(row["id"])

    result = bind.execute(
        category.insert().values(
            category_key=CATEGORY_KEY,
            name=CATEGORY_NAME,
            description=CATEGORY_DESCRIPTION,
            sort_order=CATEGORY_SORT_ORDER,
            status="active",
            version=1,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    inserted = result.inserted_primary_key
    return int(inserted[0]) if inserted and inserted[0] is not None else None


def _backfill_consistent_pool_tasks(bind, category_id: int) -> None:
    tables = set(inspect(bind).get_table_names())
    if "task" not in tables:
        return
    metadata = sa.MetaData()
    task = sa.Table("task", metadata, autoload_with=bind)
    required_columns = {
        "source_type",
        "config",
        "dataset_category_id",
        "deleted_at",
    }
    if not required_columns.issubset(task.c.keys()):
        return
    bind.execute(
        task.update()
        .where(
            task.c.deleted_at.is_(None),
            sa.or_(
                task.c.source_type == "consistent_pool",
                task.c.config.like("%train_filtered_consistent%"),
            ),
        )
        .values(dataset_category_id=int(category_id))
    )


def upgrade() -> None:
    bind = op.get_bind()
    category_id = _ensure_category(bind)
    if category_id is not None:
        _backfill_consistent_pool_tasks(bind, category_id)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "dataset_category" not in tables:
        return
    metadata = sa.MetaData()
    category = sa.Table("dataset_category", metadata, autoload_with=bind)
    category_id = bind.execute(
        sa.select(category.c.id).where(category.c.category_key == CATEGORY_KEY)
    ).scalar()
    if category_id is None:
        return

    if "task" in tables:
        task = sa.Table("task", metadata, autoload_with=bind)
        bind.execute(
            task.update()
            .where(
                task.c.dataset_category_id == int(category_id),
                sa.or_(
                    task.c.source_type == "consistent_pool",
                    task.c.config.like("%train_filtered_consistent%"),
                ),
            )
            .values(dataset_category_id=None)
        )

    references = (
        ("task", "dataset_category_id"),
        ("yolo_train_job", "dataset_category_id"),
        ("training_task", "dataset_category_id"),
        ("strategy_task_integration", "dataset_category_id"),
    )
    for table_name, column_name in references:
        if table_name not in tables:
            continue
        table = sa.Table(table_name, metadata, autoload_with=bind)
        if column_name not in table.c:
            continue
        count = bind.execute(
            sa.select(sa.func.count()).select_from(table).where(
                table.c[column_name] == int(category_id)
            )
        ).scalar()
        if int(count or 0) > 0:
            return
    bind.execute(category.delete().where(category.c.id == int(category_id)))
