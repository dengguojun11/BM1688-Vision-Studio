"""yolo_train_job: category and hyperparams_json

Revision ID: f7b8c9d0e1f2
Revises: e6f7a8b9c0d1
Create Date: 2026-05-11

"""
from alembic import op
import sqlalchemy as sa

revision = "f7b8c9d0e1f2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_train_job" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
    if "category" not in cols:
        op.add_column(
            "yolo_train_job",
            sa.Column("category", sa.String(length=128), nullable=True, server_default=""),
        )
    if "hyperparams_json" not in cols:
        op.add_column(
            "yolo_train_job",
            sa.Column("hyperparams_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_train_job" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("yolo_train_job")}
    if "hyperparams_json" in cols:
        op.drop_column("yolo_train_job", "hyperparams_json")
    if "category" in cols:
        op.drop_column("yolo_train_job", "category")
