"""Enforce one pipeline-stage record per automatic-training round.

The ORM declares ``auto_training_round_id`` as unique on each stage entity,
but the historical production migrations only added the columns.  This
forward-only migration closes that database-level concurrency gap without
rewriting old rows.  Existing duplicate data is a migration blocker and must
be reconciled from an approved backup/data plan before retrying.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "zf2a3b4c5d6e"
down_revision = "ze1f2a3b4c5d"
branch_labels = None
depends_on = None


_TABLES = (
    "training_task",
    "yolo_train_job",
    "yolo_bm1688_export",
    "yolo_edge_validation_job",
)

_INDEXES = {table: f"uq_{table}_auto_training_round" for table in _TABLES}


def _inspector():
    return inspect(op.get_bind())


def _duplicate_round_ids(table_name: str) -> list[tuple[int, int]]:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT auto_training_round_id, COUNT(*) AS row_count "
            f"FROM {table_name} "
            "WHERE auto_training_round_id IS NOT NULL "
            "GROUP BY auto_training_round_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY auto_training_round_id "
            "LIMIT 20"
        )
    ).all()
    return [(int(row[0]), int(row[1])) for row in rows]


def _index_details(table_name: str) -> dict[str, dict[str, object]]:
    return {
        str(item.get("name")): item
        for item in _inspector().get_indexes(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    inspector = _inspector()
    existing_tables = set(inspector.get_table_names())
    missing_tables = sorted(set(_TABLES) - existing_tables)
    if missing_tables:
        raise RuntimeError(
            "自动训练轮次唯一性迁移要求前置版本已创建目标表："
            + ", ".join(missing_tables)
        )

    for table_name in _TABLES:
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        if "auto_training_round_id" not in columns:
            raise RuntimeError(
                f"{table_name} 缺少 auto_training_round_id，禁止创建唯一索引"
            )
        duplicates = _duplicate_round_ids(table_name)
        if duplicates:
            rendered = ", ".join(
                f"轮次 {round_id} ({count} 条)" for round_id, count in duplicates
            )
            raise RuntimeError(
                f"{table_name} 存在重复 auto_training_round_id，"
                f"迁移已阻断：{rendered}"
            )

    for table_name, index_name in _INDEXES.items():
        indexes = _index_details(table_name)
        existing = indexes.get(index_name)
        if existing is None:
            op.create_index(
                index_name,
                table_name,
                ["auto_training_round_id"],
                unique=True,
            )
        elif not bool(existing.get("unique")):
            raise RuntimeError(
                f"{table_name}.{index_name} 已存在但不是唯一索引，禁止静默覆盖"
            )


def downgrade() -> None:
    raise RuntimeError(
        "自动训练轮次唯一性迁移为前向兼容结构变更；生产恢复必须使用迁移前备份，"
        "禁止自动删除唯一索引"
    )
