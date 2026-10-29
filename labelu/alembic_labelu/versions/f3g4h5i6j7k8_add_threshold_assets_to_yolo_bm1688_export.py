"""add threshold assets to yolo_bm1688_export

Revision ID: f3g4h5i6j7k8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-20 11:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f3g4h5i6j7k8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_bm1688_export" not in inspector.get_table_names():
        return

    column_names = {
        item["name"] for item in inspector.get_columns("yolo_bm1688_export")
    }
    if "business_thresholds_json" not in column_names:
        op.add_column(
            "yolo_bm1688_export",
            sa.Column("business_thresholds_json", sa.Text(), nullable=True),
        )
    if "validation_thresholds_json" not in column_names:
        op.add_column(
            "yolo_bm1688_export",
            sa.Column("validation_thresholds_json", sa.Text(), nullable=True),
        )
    if "threshold_binding_json" not in column_names:
        op.add_column(
            "yolo_bm1688_export",
            sa.Column("threshold_binding_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_bm1688_export" not in inspector.get_table_names():
        return

    column_names = {
        item["name"] for item in inspector.get_columns("yolo_bm1688_export")
    }
    if "threshold_binding_json" in column_names:
        op.drop_column("yolo_bm1688_export", "threshold_binding_json")
    if "validation_thresholds_json" in column_names:
        op.drop_column("yolo_bm1688_export", "validation_thresholds_json")
    if "business_thresholds_json" in column_names:
        op.drop_column("yolo_bm1688_export", "business_thresholds_json")
