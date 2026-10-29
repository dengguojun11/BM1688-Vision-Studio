"""Move active-learning clustering and batches to durable evolution scopes.

Historical rows retain ``project_id``. New rows use ``scope_key`` (currently
``global`` for third-party edge feedback), so NULL is never part of an
idempotency key. Model and source-package dimensions make a global queue
reproducible and prevent unrelated feedback from being mixed.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import case, cast, func, inspect, Integer, String, update


revision = "z9d1e2f3a4b5"
down_revision = "z9c0d1e2f3a4"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def _uniques(table: str) -> set[str]:
    return {item.get("name") or "" for item in inspect(op.get_bind()).get_unique_constraints(table)}


def _indexes(table: str) -> set[str]:
    return {item.get("name") or "" for item in inspect(op.get_bind()).get_indexes(table)}


def _add_columns(table: str, additions: list[sa.Column]) -> None:
    columns = _columns(table)
    for column in additions:
        if column.name not in columns:
            op.add_column(table, column)


def _backfill_scope(table: str) -> None:
    bind = op.get_bind()
    table_obj = sa.table(
        table,
        sa.column("project_id", Integer),
        sa.column("scope_key", String(160)),
    )
    project_scope = (
        func.concat("project:", cast(table_obj.c.project_id, String(32)))
        if bind.dialect.name == "mysql"
        else "project:" + cast(table_obj.c.project_id, String(32))
    )
    bind.execute(
        update(table_obj)
        .where(table_obj.c.scope_key.is_(None))
        .values(scope_key=case((table_obj.c.project_id.is_(None), "global"), else_=project_scope))
    )


def _replace_unique(
    table: str,
    old_name: str,
    new_name: str,
    columns: tuple[str, ...],
    nullable_project: bool = True,
) -> None:
    bind = op.get_bind()
    uniques = _uniques(table)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            if old_name in uniques:
                batch.drop_constraint(old_name, type_="unique")
            if nullable_project and "project_id" in _columns(table):
                batch.alter_column("project_id", existing_type=sa.Integer(), nullable=True)
            batch.alter_column("scope_key", existing_type=sa.String(160), nullable=False)
            if new_name not in uniques:
                batch.create_unique_constraint(new_name, list(columns))
        return
    if old_name in uniques:
        op.drop_constraint(old_name, table_name=table, type_="unique")
    if nullable_project and "project_id" in _columns(table):
        op.alter_column(table, "project_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column(table, "scope_key", existing_type=sa.String(160), nullable=False)
    if new_name not in _uniques(table):
        op.create_unique_constraint(new_name, table, list(columns))


def _index(table: str, name: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    for table in ("feedback_cluster", "active_learning_batch"):
        if table not in tables:
            return

    _add_columns(
        "feedback_cluster",
        [
            sa.Column("scope_key", sa.String(160), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("model_version_id", sa.Integer(), nullable=True),
            sa.Column("source_key", sa.String(160), nullable=True),
        ],
    )
    _add_columns(
        "active_learning_batch",
        [
            sa.Column("scope_key", sa.String(160), nullable=True),
            sa.Column("model_version_id", sa.Integer(), nullable=True),
            sa.Column("source_key", sa.String(160), nullable=True),
        ],
    )
    _backfill_scope("feedback_cluster")
    _backfill_scope("active_learning_batch")

    _replace_unique(
        "feedback_cluster",
        "uq_feedback_cluster_project_key",
        "uq_feedback_cluster_scope_key",
        ("scope_key", "cluster_key"),
    )
    _replace_unique(
        "active_learning_batch",
        "uq_active_learning_batch_key",
        "uq_active_learning_batch_scope_key",
        ("scope_key", "batch_key"),
    )
    _index("feedback_cluster", "idx_feedback_cluster_scope_status", ["scope_key", "status", "updated_at"])
    _index("active_learning_batch", "idx_active_learning_batch_scope_status", ["scope_key", "status", "updated_at"])
    _index("feedback_cluster", "ix_feedback_cluster_model_version_id", ["model_version_id"])
    _index("feedback_cluster", "ix_feedback_cluster_source_key", ["source_key"])
    _index("active_learning_batch", "ix_active_learning_batch_model_version_id", ["model_version_id"])
    _index("active_learning_batch", "ix_active_learning_batch_source_key", ["source_key"])


def downgrade() -> None:
    raise RuntimeError("主动学习范围迁移涉及全局幂等数据，生产降级必须恢复备份")
