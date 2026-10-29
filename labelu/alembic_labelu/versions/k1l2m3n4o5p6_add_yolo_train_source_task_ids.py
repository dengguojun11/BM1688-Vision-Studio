"""add yolo_train_job.source_task_ids_json for multi-task merged training

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-05-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_train_job" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
    if "source_task_ids_json" not in cols:
        op.add_column(
            "yolo_train_job",
            sa.Column("source_task_ids_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_train_job" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
    if "source_task_ids_json" in cols:
        op.drop_column("yolo_train_job", "source_task_ids_json")
