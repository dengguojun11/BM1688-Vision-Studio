"""Expand plate component JSON columns to LONGTEXT on MySQL.

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None


_COMPONENT_JSON_COLUMNS = (
    "component_status_json",
    "component_artifacts_json",
)


def _existing_component_json_columns() -> list[str]:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_train_job" not in set(inspector.get_table_names()):
        return []
    column_names = {item["name"] for item in inspector.get_columns("yolo_train_job")}
    return [name for name in _COMPONENT_JSON_COLUMNS if name in column_names]


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for column_name in _existing_component_json_columns():
        op.alter_column(
            "yolo_train_job",
            column_name,
            existing_type=sa.Text(),
            type_=mysql.LONGTEXT(),
            existing_nullable=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for column_name in _existing_component_json_columns():
        op.alter_column(
            "yolo_train_job",
            column_name,
            existing_type=mysql.LONGTEXT(),
            type_=mysql.TEXT(),
            existing_nullable=True,
        )
