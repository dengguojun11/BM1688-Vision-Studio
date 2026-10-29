"""add yolo_board_validation for BM1688 board agent

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-05-25

"""
from alembic import op
import sqlalchemy as sa

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "yolo_board_validation" in inspector.get_table_names():
        return
    op.create_table(
        "yolo_board_validation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("export_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("yolo_train_job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["export_id"], ["yolo_bm1688_export.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["yolo_train_job_id"], ["yolo_train_job.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_yolo_board_validation_id", "yolo_board_validation", ["id"])
    op.create_index(
        "ix_yolo_board_validation_export_id", "yolo_board_validation", ["export_id"]
    )
    op.create_index(
        "ix_yolo_board_validation_status", "yolo_board_validation", ["status"]
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "yolo_board_validation" not in inspector.get_table_names():
        return
    op.drop_index("ix_yolo_board_validation_status", table_name="yolo_board_validation")
    op.drop_index("ix_yolo_board_validation_export_id", table_name="yolo_board_validation")
    op.drop_index("ix_yolo_board_validation_id", table_name="yolo_board_validation")
    op.drop_table("yolo_board_validation")
