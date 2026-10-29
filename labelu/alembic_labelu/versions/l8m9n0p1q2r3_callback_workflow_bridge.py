"""Link legacy strategy callbacks to the unified 7GAI workflow outbox."""

from alembic import op
import sqlalchemy as sa


revision = "l8m9n0p1q2r3"
down_revision = "k7l8m9n0p1q2"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def _foreign_keys(bind, table):
    return {item.get("name") for item in sa.inspect(bind).get_foreign_keys(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "strategy_task_integration" not in tables:
        return
    if "callback_workflow_id" not in _columns(bind, "strategy_task_integration"):
        op.add_column(
            "strategy_task_integration",
            sa.Column("callback_workflow_id", sa.Integer(), nullable=True),
        )
    if "ix_strategy_task_integration_callback_workflow_id" not in _indexes(
        bind, "strategy_task_integration"
    ):
        op.create_index(
            "ix_strategy_task_integration_callback_workflow_id",
            "strategy_task_integration",
            ["callback_workflow_id"],
            unique=False,
        )
    # SQLite does not support adding a foreign key constraint through ALTER
    # TABLE.  The ORM still enforces the relationship, while MySQL receives a
    # real FK on normal upgrades.
    if (
        "workflow_instance" in tables
        and bind.dialect.name != "sqlite"
        and "fk_strategy_callback_workflow" not in _foreign_keys(
            bind, "strategy_task_integration"
        )
    ):
        op.create_foreign_key(
            "fk_strategy_callback_workflow",
            "strategy_task_integration",
            "workflow_instance",
            ["callback_workflow_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "strategy_task_integration" not in tables:
        return
    if (
        bind.dialect.name != "sqlite"
        and "fk_strategy_callback_workflow" in _foreign_keys(
            bind, "strategy_task_integration"
        )
    ):
        op.drop_constraint(
            "fk_strategy_callback_workflow",
            "strategy_task_integration",
            type_="foreignkey",
        )
    if "ix_strategy_task_integration_callback_workflow_id" in _indexes(
        bind, "strategy_task_integration"
    ):
        op.drop_index(
            "ix_strategy_task_integration_callback_workflow_id",
            table_name="strategy_task_integration",
        )
    if "callback_workflow_id" in _columns(bind, "strategy_task_integration"):
        op.drop_column("strategy_task_integration", "callback_workflow_id")

