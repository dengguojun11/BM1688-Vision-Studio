"""add sample bounds check job table

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-06-04 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "sample_bounds_check_job" not in tables:
        op.create_table(
            "sample_bounds_check_job",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=True),
            sa.Column("processed_count", sa.Integer(), nullable=True),
            sa.Column("normal_count", sa.Integer(), nullable=True),
            sa.Column("abnormal_count", sa.Integer(), nullable=True),
            sa.Column("failed_count", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_sample_bounds_check_job_id", "sample_bounds_check_job", ["id"])
        op.create_index("ix_sample_bounds_check_job_task_id", "sample_bounds_check_job", ["task_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "sample_bounds_check_job" in tables:
        op.drop_index("ix_sample_bounds_check_job_task_id", table_name="sample_bounds_check_job")
        op.drop_index("ix_sample_bounds_check_job_id", table_name="sample_bounds_check_job")
        op.drop_table("sample_bounds_check_job")
