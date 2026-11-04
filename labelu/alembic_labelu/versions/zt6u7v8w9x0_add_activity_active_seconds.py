"""add active interaction columns to user activity sessions

Revision ID: zt6u7v8w9x0
Revises: zs5t6u7v8w9
Create Date: 2026-11-04 21:25:00
"""
from alembic import op
import sqlalchemy as sa


revision = "zt6u7v8w9x0"
down_revision = "zs5t6u7v8w9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_activity_session",
        sa.Column("active_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "user_activity_session",
        sa.Column("active_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_user_activity_session_active_date",
        "user_activity_session",
        ["active_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_activity_session_active_date",
        table_name="user_activity_session",
    )
    op.drop_column("user_activity_session", "active_date")
    op.drop_column("user_activity_session", "active_seconds")
