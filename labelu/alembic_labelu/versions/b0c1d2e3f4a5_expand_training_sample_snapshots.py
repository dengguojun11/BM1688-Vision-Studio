"""Expand training sample snapshot JSON columns for large datasets.

Revision ID: b0c1d2e3f4a5
Revises: z8a9b0c1d2e3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql


revision = "b0c1d2e3f4a5"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("training_task", "source_sample_snapshot_json"),
    ("yolo_train_job", "source_sample_manifest_json"),
)


def _existing_columns():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    result = []
    for table_name, column_name in _COLUMNS:
        if table_name not in tables:
            continue
        columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
        if column_name in columns:
            result.append((table_name, column_name))
    return result


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    for table_name, column_name in _existing_columns():
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Text(),
            type_=mysql.LONGTEXT(),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    for table_name, column_name in _existing_columns():
        op.alter_column(
            table_name,
            column_name,
            existing_type=mysql.LONGTEXT(),
            type_=mysql.TEXT(),
            existing_nullable=True,
        )
