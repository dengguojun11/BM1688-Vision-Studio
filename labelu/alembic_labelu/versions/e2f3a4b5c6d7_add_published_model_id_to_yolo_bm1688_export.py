"""add published_model_id to yolo_bm1688_export

Revision ID: e2f3a4b5c6d7
Revises: d9e0f1a2b3c4
Create Date: 2026-07-17 13:25:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e2f3a4b5c6d7"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_bm1688_export" not in inspector.get_table_names():
        return

    column_names = {item["name"] for item in inspector.get_columns("yolo_bm1688_export")}
    if "published_model_id" not in column_names:
        op.add_column(
            "yolo_bm1688_export",
            sa.Column("published_model_id", sa.Integer(), nullable=True),
        )

    indexes = {item["name"] for item in inspector.get_indexes("yolo_bm1688_export")}
    if "ix_yolo_bm1688_export_published_model_id" not in indexes:
        op.create_index(
            "ix_yolo_bm1688_export_published_model_id",
            "yolo_bm1688_export",
            ["published_model_id"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_bm1688_export" not in inspector.get_table_names():
        return

    indexes = {item["name"] for item in inspector.get_indexes("yolo_bm1688_export")}
    if "ix_yolo_bm1688_export_published_model_id" in indexes:
        op.drop_index(
            "ix_yolo_bm1688_export_published_model_id",
            table_name="yolo_bm1688_export",
        )

    column_names = {item["name"] for item in inspector.get_columns("yolo_bm1688_export")}
    if "published_model_id" in column_names:
        op.drop_column("yolo_bm1688_export", "published_model_id")
