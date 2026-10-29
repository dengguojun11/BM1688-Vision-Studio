"""add sample edit log table

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-06-30 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "u2v3w4x5y6z7"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "sample_edit_log" not in tables:
        op.create_table(
            "sample_edit_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("previous_state", sa.String(length=32), nullable=True),
            sa.Column("new_state", sa.String(length=32), nullable=True),
            sa.Column("previous_annotated_count", sa.Integer(), nullable=True),
            sa.Column("new_annotated_count", sa.Integer(), nullable=True),
            sa.Column("previous_data", sa.Text(), nullable=True),
            sa.Column("new_data", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["sample_id"], ["task_sample.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_sample_edit_log_id", "sample_edit_log", ["id"])
        op.create_index("ix_sample_edit_log_task_id", "sample_edit_log", ["task_id"])
        op.create_index("ix_sample_edit_log_sample_id", "sample_edit_log", ["sample_id"])
        op.create_index("ix_sample_edit_log_user_id", "sample_edit_log", ["user_id"])
        op.create_index("ix_sample_edit_log_created_at", "sample_edit_log", ["created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "sample_edit_log" in tables:
        op.drop_index("ix_sample_edit_log_created_at", table_name="sample_edit_log")
        op.drop_index("ix_sample_edit_log_user_id", table_name="sample_edit_log")
        op.drop_index("ix_sample_edit_log_sample_id", table_name="sample_edit_log")
        op.drop_index("ix_sample_edit_log_task_id", table_name="sample_edit_log")
        op.drop_index("ix_sample_edit_log_id", table_name="sample_edit_log")
        op.drop_table("sample_edit_log")
