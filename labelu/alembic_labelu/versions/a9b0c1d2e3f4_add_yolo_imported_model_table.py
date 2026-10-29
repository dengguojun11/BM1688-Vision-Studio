"""add yolo_imported_model table for external .pt uploads

Revision ID: a9b0c1d2e3f4
Revises: f7b8c9d0e1f2
Create Date: 2026-05-11

"""
from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "f7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_imported_model" in tables:
        return
    op.create_table(
        "yolo_imported_model",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("original_filename", sa.String(length=256), nullable=True),
        sa.Column("weights_rel_path", sa.String(length=512), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_yolo_imported_model_id", "yolo_imported_model", ["id"])
    op.create_index("ix_yolo_imported_model_user_id", "yolo_imported_model", ["user_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "yolo_imported_model" not in tables:
        return
    op.drop_index("ix_yolo_imported_model_user_id", table_name="yolo_imported_model")
    op.drop_index("ix_yolo_imported_model_id", table_name="yolo_imported_model")
    op.drop_table("yolo_imported_model")
