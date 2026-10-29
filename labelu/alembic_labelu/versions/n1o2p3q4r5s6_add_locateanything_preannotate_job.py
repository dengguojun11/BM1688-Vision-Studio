"""add locateanything_preannotate_job table

Revision ID: n1o2p3q4r5s6
Revises: q7r8s9t0u1v2
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa


revision = "n1o2p3q4r5s6"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "locateanything_preannotate_job" in tables:
        return
    op.create_table(
        "locateanything_preannotate_job",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("task.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_locateanything_preannotate_job_id", "locateanything_preannotate_job", ["id"])
    op.create_index("ix_locateanything_preannotate_job_task_id", "locateanything_preannotate_job", ["task_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "locateanything_preannotate_job" not in tables:
        return
    op.drop_index("ix_locateanything_preannotate_job_task_id", table_name="locateanything_preannotate_job")
    op.drop_index("ix_locateanything_preannotate_job_id", table_name="locateanything_preannotate_job")
    op.drop_table("locateanything_preannotate_job")
