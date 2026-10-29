"""expand experiment dataset import metadata_json for mysql

Revision ID: d9e0f1a2b3c4
Revises: c7d8e9f0a1b2
Create Date: 2026-07-16 20:40:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql


revision = "d9e0f1a2b3c4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "experiment_dataset_import" not in inspector.get_table_names():
        return
    if conn.dialect.name != "mysql":
        return
    column_names = {item["name"] for item in inspector.get_columns("experiment_dataset_import")}
    if "metadata_json" not in column_names:
        return
    op.alter_column(
        "experiment_dataset_import",
        "metadata_json",
        existing_type=sa.Text(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "experiment_dataset_import" not in inspector.get_table_names():
        return
    if conn.dialect.name != "mysql":
        return
    column_names = {item["name"] for item in inspector.get_columns("experiment_dataset_import")}
    if "metadata_json" not in column_names:
        return
    op.alter_column(
        "experiment_dataset_import",
        "metadata_json",
        existing_type=mysql.LONGTEXT(),
        type_=mysql.TEXT(),
        existing_nullable=True,
    )
