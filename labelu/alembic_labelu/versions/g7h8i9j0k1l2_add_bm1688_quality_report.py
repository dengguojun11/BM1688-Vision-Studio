"""add quality_report to yolo_bm1688_export

Revision ID: g7h8i9j0k1l2
Revises: f1c2b3a4d5e6
Create Date: 2026-05-20

"""
from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "f1c2b3a4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns("yolo_bm1688_export")}
    if "quality_report" in columns:
        return
    op.add_column(
        "yolo_bm1688_export",
        sa.Column("quality_report", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c["name"] for c in inspector.get_columns("yolo_bm1688_export")}
    if "quality_report" not in columns:
        return
    op.drop_column("yolo_bm1688_export", "quality_report")
