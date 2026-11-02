"""add system operation log

Revision ID: zq3r4s5t6u7
Revises: zp2q3r4s5t6
Create Date: 2026-11-02 13:50:00
"""
from alembic import op
import sqlalchemy as sa


revision = "zq3r4s5t6u7"
down_revision = "zp2q3r4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_operation_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.String(length=512), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_operation_log_id", "system_operation_log", ["id"])
    op.create_index(
        "ix_system_operation_log_category", "system_operation_log", ["category"]
    )
    op.create_index("ix_system_operation_log_action", "system_operation_log", ["action"])
    op.create_index("ix_system_operation_log_user_id", "system_operation_log", ["user_id"])
    op.create_index(
        "ix_system_operation_log_request_id", "system_operation_log", ["request_id"]
    )
    op.create_index("ix_system_operation_log_path", "system_operation_log", ["path"])
    op.create_index(
        "ix_system_operation_log_status_code", "system_operation_log", ["status_code"]
    )
    op.create_index(
        "ix_system_operation_log_created_at", "system_operation_log", ["created_at"]
    )
    op.create_index(
        "ix_system_operation_log_category_created",
        "system_operation_log",
        ["category", "created_at"],
    )
    op.create_index(
        "ix_system_operation_log_user_created",
        "system_operation_log",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("system_operation_log")
