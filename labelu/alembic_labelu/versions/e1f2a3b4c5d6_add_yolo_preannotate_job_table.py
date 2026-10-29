"""add yolo_preannotate_job for batch YOLO inference pre-annotations

Revision ID: e1f2a3b4c5d6
Revises: c0d1e2f3a4b6
Create Date: 2026-05-10

"""

from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "c0d1e2f3a4b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_preannotate_job" in tables:
        return
    op.create_table(
        "yolo_preannotate_job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=True),
        sa.Column("model_kind", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_yolo_preannotate_job_id", "yolo_preannotate_job", ["id"])
    op.create_index("ix_yolo_preannotate_job_task_id", "yolo_preannotate_job", ["task_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_preannotate_job" not in tables:
        return
    op.drop_index("ix_yolo_preannotate_job_task_id", table_name="yolo_preannotate_job")
    op.drop_index("ix_yolo_preannotate_job_id", table_name="yolo_preannotate_job")
    op.drop_table("yolo_preannotate_job")
