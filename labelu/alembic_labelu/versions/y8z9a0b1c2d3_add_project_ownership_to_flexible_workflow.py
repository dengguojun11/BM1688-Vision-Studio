"""Add project ownership to strategy integrations and third-party audits.

The columns remain nullable so existing installations can upgrade without
inventing a tenant for historical rows. Deterministic links are backfilled
from task/project relationships and strategy external identifiers.
"""

from alembic import op
import sqlalchemy as sa


revision = "y8z9a0b1c2d3"
down_revision = "x7y8z9a0b1c2"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def _foreign_keys(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_foreign_keys(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "strategy_task_integration" in tables:
        if "project_id" not in _columns(bind, "strategy_task_integration"):
            op.add_column(
                "strategy_task_integration",
                sa.Column("project_id", sa.Integer(), nullable=True),
            )
        if "ix_strategy_task_integration_project_id" not in _indexes(
            bind, "strategy_task_integration"
        ):
            op.create_index(
                "ix_strategy_task_integration_project_id",
                "strategy_task_integration",
                ["project_id"],
                unique=False,
            )
        if (
            "project" in tables
            and bind.dialect.name != "sqlite"
            and "fk_strategy_task_integration_project_id_project"
            not in _foreign_keys(bind, "strategy_task_integration")
        ):
            op.create_foreign_key(
                "fk_strategy_task_integration_project_id_project",
                "strategy_task_integration",
                "project",
                ["project_id"],
                ["id"],
            )
        # Existing imported tasks are the authoritative source of tenant
        # ownership. Leave orphaned rows NULL for explicit operator review.
        if "task" in tables:
            bind.execute(
                sa.text(
                    "UPDATE strategy_task_integration "
                    "SET project_id = (SELECT project_id FROM task "
                    "WHERE task.id = strategy_task_integration.labelu_task_id) "
                    "WHERE project_id IS NULL AND labelu_task_id IS NOT NULL"
                )
            )

    if "third_party_call_audit" in tables:
        if "project_id" not in _columns(bind, "third_party_call_audit"):
            op.add_column(
                "third_party_call_audit",
                sa.Column("project_id", sa.Integer(), nullable=True),
            )
        if "ix_third_party_call_audit_project_id" not in _indexes(
            bind, "third_party_call_audit"
        ):
            op.create_index(
                "ix_third_party_call_audit_project_id",
                "third_party_call_audit",
                ["project_id"],
                unique=False,
            )
        if (
            "project" in tables
            and bind.dialect.name != "sqlite"
            and "fk_third_party_call_audit_project_id_project"
            not in _foreign_keys(bind, "third_party_call_audit")
        ):
            op.create_foreign_key(
                "fk_third_party_call_audit_project_id_project",
                "third_party_call_audit",
                "project",
                ["project_id"],
                ["id"],
            )
        if "strategy_task_integration" in tables:
            # Prefer task identifiers, then package identifiers when a
            # strategy integration carries the same external package key.
            bind.execute(
                sa.text(
                    "UPDATE third_party_call_audit "
                    "SET project_id = (SELECT project_id FROM strategy_task_integration "
                    "WHERE strategy_task_integration.external_task_id = "
                    "third_party_call_audit.external_task_id "
                    "AND strategy_task_integration.project_id IS NOT NULL "
                    "ORDER BY strategy_task_integration.id DESC LIMIT 1) "
                    "WHERE project_id IS NULL AND external_task_id IS NOT NULL"
                )
            )
            bind.execute(
                sa.text(
                    "UPDATE third_party_call_audit "
                    "SET project_id = (SELECT project_id FROM strategy_task_integration "
                    "WHERE strategy_task_integration.source_package_id = "
                    "third_party_call_audit.external_package_id "
                    "AND strategy_task_integration.project_id IS NOT NULL "
                    "ORDER BY strategy_task_integration.id DESC LIMIT 1) "
                    "WHERE project_id IS NULL AND external_package_id IS NOT NULL"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "third_party_call_audit" in tables and "project_id" in _columns(
        bind, "third_party_call_audit"
    ):
        if (
            bind.dialect.name != "sqlite"
            and "fk_third_party_call_audit_project_id_project"
            in _foreign_keys(bind, "third_party_call_audit")
        ):
            op.drop_constraint(
                "fk_third_party_call_audit_project_id_project",
                "third_party_call_audit",
                type_="foreignkey",
            )
        if "ix_third_party_call_audit_project_id" in _indexes(
            bind, "third_party_call_audit"
        ):
            op.drop_index(
                "ix_third_party_call_audit_project_id",
                table_name="third_party_call_audit",
            )
        op.drop_column("third_party_call_audit", "project_id")
    if "strategy_task_integration" in tables and "project_id" in _columns(
        bind, "strategy_task_integration"
    ):
        if (
            bind.dialect.name != "sqlite"
            and "fk_strategy_task_integration_project_id_project"
            in _foreign_keys(bind, "strategy_task_integration")
        ):
            op.drop_constraint(
                "fk_strategy_task_integration_project_id_project",
                "strategy_task_integration",
                type_="foreignkey",
            )
        if "ix_strategy_task_integration_project_id" in _indexes(
            bind, "strategy_task_integration"
        ):
            op.drop_index(
                "ix_strategy_task_integration_project_id",
                table_name="strategy_task_integration",
            )
        op.drop_column("strategy_task_integration", "project_id")
