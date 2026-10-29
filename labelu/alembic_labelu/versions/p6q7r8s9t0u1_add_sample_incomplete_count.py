"""add sample bounds incomplete count

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-06-09 14:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {
        col["name"]
        for col in inspector.get_columns("sample_bounds_check_job")
    }
    if "incomplete_count" not in columns:
        op.add_column(
            "sample_bounds_check_job",
            sa.Column("incomplete_count", sa.Integer(), nullable=True, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {
        col["name"]
        for col in inspector.get_columns("sample_bounds_check_job")
    }
    if "incomplete_count" in columns:
        op.drop_column("sample_bounds_check_job", "incomplete_count")
