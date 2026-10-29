"""expand yolo edge validation result_json for mysql

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-06-29

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql


revision = "t1u2v3w4x5y6"
down_revision = "s0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    if conn.dialect.name != "mysql":
        return
    op.alter_column(
        "yolo_edge_validation_job",
        "result_json",
        existing_type=sa.Text(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    if conn.dialect.name != "mysql":
        return
    op.alter_column(
        "yolo_edge_validation_job",
        "result_json",
        existing_type=mysql.LONGTEXT(),
        type_=mysql.TEXT(),
        existing_nullable=True,
    )
