"""Expand strategy task automation payload columns for MySQL.

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
"""

from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql


revision = "r9s0t1u2v3w4"
down_revision = "q8r9s0t1u2v3"
branch_labels = None
depends_on = None


_PAYLOAD_COLUMNS = (
    "platform_result_json",
    "platform_error_json",
    "automation_result_json",
    "last_error",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    inspector = inspect(bind)
    if "strategy_task_integration" not in inspector.get_table_names():
        return

    columns = {item["name"]: item for item in inspector.get_columns("strategy_task_integration")}
    for column_name in _PAYLOAD_COLUMNS:
        if column_name not in columns:
            continue
        current_type = str(columns[column_name]["type"]).upper()
        if "LONGTEXT" in current_type:
            continue
        op.alter_column(
            "strategy_task_integration",
            column_name,
            existing_type=columns[column_name]["type"],
            type_=mysql.LONGTEXT(),
            existing_nullable=bool(columns[column_name].get("nullable", True)),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    inspector = inspect(bind)
    if "strategy_task_integration" not in inspector.get_table_names():
        return

    columns = {item["name"]: item for item in inspector.get_columns("strategy_task_integration")}
    for column_name in _PAYLOAD_COLUMNS:
        if column_name not in columns:
            continue
        op.alter_column(
            "strategy_task_integration",
            column_name,
            existing_type=columns[column_name]["type"],
            type_=mysql.TEXT(),
            existing_nullable=bool(columns[column_name].get("nullable", True)),
        )
