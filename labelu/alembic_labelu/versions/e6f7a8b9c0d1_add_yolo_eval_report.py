"""add yolo_eval_report and ai eval status on yolo_train_job

Revision ID: e6f7a8b9c0d1
Revises: b3c4d5e6f7a8
Create Date: 2026-05-10

"""
from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c0d1"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "yolo_eval_report" not in tables:
        op.create_table(
            "yolo_eval_report",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("yolo_train_job_id", sa.Integer(), nullable=True),
            sa.Column("provider_key", sa.String(length=32), nullable=False),
            sa.Column("report_kind", sa.String(length=32), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("suggested_params", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["yolo_train_job_id"], ["yolo_train_job.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_yolo_eval_report_id", "yolo_eval_report", ["id"])
        op.create_index(
            "ix_yolo_eval_report_yolo_train_job_id",
            "yolo_eval_report",
            ["yolo_train_job_id"],
        )

    if "yolo_train_job" in tables:
        cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
        if "ai_eval_full_status" not in cols:
            op.add_column(
                "yolo_train_job",
                sa.Column("ai_eval_full_status", sa.String(length=16), nullable=True),
            )
        if "ai_eval_next_status" not in cols:
            op.add_column(
                "yolo_train_job",
                sa.Column("ai_eval_next_status", sa.String(length=16), nullable=True),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "yolo_train_job" in inspector.get_table_names():
        for col in ("ai_eval_next_status", "ai_eval_full_status"):
            inspector = sa.inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
            if col in cols:
                op.drop_column("yolo_train_job", col)

    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_eval_report" in tables:
        op.drop_index("ix_yolo_eval_report_yolo_train_job_id", table_name="yolo_eval_report")
        op.drop_index("ix_yolo_eval_report_id", table_name="yolo_eval_report")
        op.drop_table("yolo_eval_report")
