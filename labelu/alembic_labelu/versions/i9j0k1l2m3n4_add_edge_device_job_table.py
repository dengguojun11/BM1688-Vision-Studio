"""add edge device job table

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-05-25

"""

from alembic import op
import sqlalchemy as sa

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "edge_device_job" in inspector.get_table_names():
        return
    op.create_table(
        "edge_device_job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("export_job_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("device_id", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["export_job_id"], ["export_job.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edge_device_job_id", "edge_device_job", ["id"])
    op.create_index("ix_edge_device_job_export_job_id", "edge_device_job", ["export_job_id"])
    op.create_index("ix_edge_device_job_task_id", "edge_device_job", ["task_id"])
    op.create_index("ix_edge_device_job_status", "edge_device_job", ["status"])
    op.create_index("ix_edge_device_job_device_id", "edge_device_job", ["device_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "edge_device_job" not in inspector.get_table_names():
        return
    op.drop_index("ix_edge_device_job_device_id", table_name="edge_device_job")
    op.drop_index("ix_edge_device_job_status", table_name="edge_device_job")
    op.drop_index("ix_edge_device_job_task_id", table_name="edge_device_job")
    op.drop_index("ix_edge_device_job_export_job_id", table_name="edge_device_job")
    op.drop_index("ix_edge_device_job_id", table_name="edge_device_job")
    op.drop_table("edge_device_job")
