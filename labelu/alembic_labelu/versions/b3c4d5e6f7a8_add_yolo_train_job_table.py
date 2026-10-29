"""add yolo_train_job table

Revision ID: b3c4d5e6f7a8
Revises: a1c2e3f4b5d6
Create Date: 2026-05-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a1c2e3f4b5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "yolo_train_job" not in tables:
        op.create_table(
            "yolo_train_job",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=True),
            sa.Column("model_weights", sa.String(length=256), nullable=True),
            sa.Column("epochs", sa.Integer(), nullable=True),
            sa.Column("imgsz", sa.Integer(), nullable=True),
            sa.Column("train_sample_count", sa.Integer(), nullable=True),
            sa.Column("val_sample_count", sa.Integer(), nullable=True),
            sa.Column("weights_path", sa.Text(), nullable=True),
            sa.Column("metrics_summary", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_yolo_train_job_id", "yolo_train_job", ["id"])
        op.create_index("ix_yolo_train_job_task_id", "yolo_train_job", ["task_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "yolo_train_job" in tables:
        op.drop_index("ix_yolo_train_job_task_id", table_name="yolo_train_job")
        op.drop_index("ix_yolo_train_job_id", table_name="yolo_train_job")
        op.drop_table("yolo_train_job")
