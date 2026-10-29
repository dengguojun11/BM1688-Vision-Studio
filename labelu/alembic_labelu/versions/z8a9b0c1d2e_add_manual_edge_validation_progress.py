"""add manual edge validation progress json

Revision ID: z8a9b0c1d2e
Revises: y7z8a9b0c1d
Create Date: 2026-07-04 10:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "z8a9b0c1d2e"
down_revision = "y7z8a9b0c1d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("yolo_edge_validation_job")}
    if "progress_json" in columns:
        return
    op.add_column(
        "yolo_edge_validation_job",
        sa.Column("progress_json", sa.Text(), nullable=True, comment="手动边端验证实时进度 JSON"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("yolo_edge_validation_job")}
    if "progress_json" not in columns:
        return
    op.drop_column("yolo_edge_validation_job", "progress_json")
