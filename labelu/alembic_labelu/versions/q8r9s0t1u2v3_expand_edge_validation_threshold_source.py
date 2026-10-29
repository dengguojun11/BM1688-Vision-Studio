"""Expand edge validation threshold source values.

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "q8r9s0t1u2v3"
down_revision = "p7q8r9s0t1u2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "yolo_edge_validation_job"
    if table_name not in inspector.get_table_names():
        return

    columns = {
        item["name"]: item
        for item in inspector.get_columns(table_name)
    }
    column = columns.get("threshold_source")
    if column is None:
        return

    op.alter_column(
        table_name,
        "threshold_source",
        existing_type=column.get("type") or sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=bool(column.get("nullable", True)),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "yolo_edge_validation_job"
    if table_name not in inspector.get_table_names():
        return

    columns = {
        item["name"]: item
        for item in inspector.get_columns(table_name)
    }
    column = columns.get("threshold_source")
    if column is None:
        return

    op.alter_column(
        table_name,
        "threshold_source",
        existing_type=column.get("type") or sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=bool(column.get("nullable", True)),
    )
