"""add yolo_train_job remote execution fields

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_train_job" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
    if "execution_mode" not in cols:
        op.add_column(
            "yolo_train_job",
            sa.Column(
                "execution_mode",
                sa.String(length=16),
                nullable=False,
                server_default="local",
            ),
        )
    if "train_node_id" not in cols:
        op.add_column(
            "yolo_train_job",
            sa.Column("train_node_id", sa.String(length=64), nullable=True),
        )
    if "remote_status_json" not in cols:
        op.add_column(
            "yolo_train_job",
            sa.Column("remote_status_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_train_job" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
    for name in ("remote_status_json", "train_node_id", "execution_mode"):
        if name in cols:
            op.drop_column("yolo_train_job", name)
