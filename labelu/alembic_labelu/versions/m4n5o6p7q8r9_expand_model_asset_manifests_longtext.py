"""Expand model asset manifests to LONGTEXT on MySQL.

Revision ID: m4n5o6p7q8r9
Revises: a2b3c4d5e6f7
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql

revision = "m4n5o6p7q8r9"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


_MANIFEST_COLUMNS = (
    ("yolo_train_job", "dataset_manifest_json"),
    ("yolo_bm1688_export", "artifact_manifest_json"),
)


def _existing_manifest_columns() -> list[tuple[str, str]]:
    conn = op.get_bind()
    inspector = inspect(conn)
    table_names = set(inspector.get_table_names())
    existing: list[tuple[str, str]] = []
    for table_name, column_name in _MANIFEST_COLUMNS:
        if table_name not in table_names:
            continue
        column_names = {item["name"] for item in inspector.get_columns(table_name)}
        if column_name in column_names:
            existing.append((table_name, column_name))
    return existing


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for table_name, column_name in _existing_manifest_columns():
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Text(),
            type_=mysql.LONGTEXT(),
            existing_nullable=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for table_name, column_name in _existing_manifest_columns():
        op.alter_column(
            table_name,
            column_name,
            existing_type=mysql.LONGTEXT(),
            type_=mysql.TEXT(),
            existing_nullable=True,
        )
