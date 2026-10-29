"""add source package linkage to strategy evolution tasks

Revision ID: n6o7p8q9r0s1
Revises: m4n5o6p7q8r9
Create Date: 2026-07-22 10:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "n6o7p8q9r0s1"
down_revision = "m4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "strategy_task_integration"
    if table_name not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns(table_name)}
    if "source_package_id" not in columns:
        op.add_column(
            table_name,
            sa.Column("source_package_id", sa.String(length=128), nullable=True),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes(table_name)}
    index_name = "ix_strategy_task_integration_source_package_id"
    if index_name not in indexes:
        op.create_index(index_name, table_name, ["source_package_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "strategy_task_integration"
    if table_name not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    index_name = "ix_strategy_task_integration_source_package_id"
    if index_name in indexes:
        op.drop_index(index_name, table_name=table_name)
    columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
    if "source_package_id" in columns:
        op.drop_column(table_name, "source_package_id")
