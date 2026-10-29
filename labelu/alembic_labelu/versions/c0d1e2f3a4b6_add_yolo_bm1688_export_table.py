"""add yolo_bm1688_export for ONNX/BModel export pipeline

Revision ID: c0d1e2f3a4b6
Revises: a9b0c1d2e3f4
Create Date: 2026-05-11

"""
from alembic import op
import sqlalchemy as sa

revision = "c0d1e2f3a4b6"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_bm1688_export" in tables:
        return
    op.create_table(
        "yolo_bm1688_export",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("yolo_train_job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("imgsz", sa.Integer(), nullable=False),
        sa.Column("onnx_rel_path", sa.String(length=512), nullable=True),
        sa.Column("bmodel_rel_path", sa.String(length=512), nullable=True),
        sa.Column("compile_log", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["yolo_train_job_id"], ["yolo_train_job.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_yolo_bm1688_export_id", "yolo_bm1688_export", ["id"])
    op.create_index("ix_yolo_bm1688_export_user_id", "yolo_bm1688_export", ["user_id"])
    op.create_index("ix_yolo_bm1688_export_task_id", "yolo_bm1688_export", ["task_id"])
    op.create_index(
        "ix_yolo_bm1688_export_yolo_train_job_id",
        "yolo_bm1688_export",
        ["yolo_train_job_id"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_bm1688_export" not in tables:
        return
    op.drop_index("ix_yolo_bm1688_export_yolo_train_job_id", table_name="yolo_bm1688_export")
    op.drop_index("ix_yolo_bm1688_export_task_id", table_name="yolo_bm1688_export")
    op.drop_index("ix_yolo_bm1688_export_user_id", table_name="yolo_bm1688_export")
    op.drop_index("ix_yolo_bm1688_export_id", table_name="yolo_bm1688_export")
    op.drop_table("yolo_bm1688_export")
