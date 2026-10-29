"""Allow new evolution workflows to run without a retired project dimension."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import case, cast, func, inspect, Integer, String, update


revision = "z9e2f3a4b5c6"
down_revision = "z9d1e2f3a4b5"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item.get("name") or "" for item in inspect(op.get_bind()).get_indexes(table)}


def _backfill_scope() -> None:
    bind = op.get_bind()
    table = sa.table(
        "workflow_instance",
        sa.column("project_id", Integer),
        sa.column("scope_key", String(160)),
    )
    project_scope = (
        func.concat("project:", cast(table.c.project_id, String(32)))
        if bind.dialect.name == "mysql"
        else "project:" + cast(table.c.project_id, String(32))
    )
    bind.execute(
        update(table)
        .where(table.c.scope_key.is_(None))
        .values(scope_key=case((table.c.project_id.is_(None), "global"), else_=project_scope))
    )


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "workflow_instance" not in tables:
        return
    if "scope_key" not in _columns("workflow_instance"):
        op.add_column(
            "workflow_instance",
            sa.Column("scope_key", sa.String(160), nullable=True),
        )
    _backfill_scope()
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("workflow_instance", recreate="always") as batch:
            batch.alter_column("project_id", existing_type=sa.Integer(), nullable=True)
            batch.alter_column("scope_key", existing_type=sa.String(160), nullable=False)
    else:
        op.alter_column("workflow_instance", "project_id", existing_type=sa.Integer(), nullable=True)
        op.alter_column("workflow_instance", "scope_key", existing_type=sa.String(160), nullable=False)
    if "idx_workflow_instance_scope_status" not in _indexes("workflow_instance"):
        op.create_index(
            "idx_workflow_instance_scope_status",
            "workflow_instance",
            ["scope_key", "status"],
            unique=False,
        )


def downgrade() -> None:
    raise RuntimeError("工作流范围迁移涉及历史幂等数据，生产降级必须恢复备份")
