"""add yolo edge validation job table

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-06-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_edge_validation_job" in inspector.get_table_names():
        return
    op.create_table(
        "yolo_edge_validation_job",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("export_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="run_only"),
        sa.Column("threshold_source", sa.String(length=32), nullable=True),
        sa.Column("sample_ids_json", sa.Text(), nullable=True),
        sa.Column("thresholds_json", sa.Text(), nullable=True),
        sa.Column("ai_thresholds_json", sa.Text(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("labeled_image_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unlabeled_image_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("report_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["export_id"], ["yolo_bm1688_export.id"]),
    )
    op.create_index("ix_yolo_edge_validation_job_id", "yolo_edge_validation_job", ["id"])
    op.create_index("ix_yolo_edge_validation_job_user_id", "yolo_edge_validation_job", ["user_id"])
    op.create_index("ix_yolo_edge_validation_job_task_id", "yolo_edge_validation_job", ["task_id"])
    op.create_index("ix_yolo_edge_validation_job_export_id", "yolo_edge_validation_job", ["export_id"])
    op.create_index("ix_yolo_edge_validation_job_status", "yolo_edge_validation_job", ["status"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    for idx in (
        "ix_yolo_edge_validation_job_status",
        "ix_yolo_edge_validation_job_export_id",
        "ix_yolo_edge_validation_job_task_id",
        "ix_yolo_edge_validation_job_user_id",
        "ix_yolo_edge_validation_job_id",
    ):
        try:
            op.drop_index(idx, table_name="yolo_edge_validation_job")
        except Exception:
            pass
    op.drop_table("yolo_edge_validation_job")
