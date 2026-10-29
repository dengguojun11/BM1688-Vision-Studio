"""Persist manual YOLO preannotation worker input and lease fencing.

Revision ID: zd1e2f3a4b5c
Revises: zc1d2e3f4a5b
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "zd1e2f3a4b5c"
down_revision = "zc1d2e3f4a5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "yolo_preannotate_job" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("yolo_preannotate_job")}
    definitions = (
        ("payload_json", sa.Column("payload_json", sa.Text(), nullable=True)),
        ("lease_owner", sa.Column("lease_owner", sa.String(128), nullable=True)),
        (
            "lease_expires_at",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        ),
        (
            "lease_fencing_token",
            sa.Column("lease_fencing_token", sa.Integer(), nullable=False, server_default="0"),
        ),
    )
    for name, column in definitions:
        if name not in columns:
            op.add_column("yolo_preannotate_job", column)
    indexes = {item.get("name") or "" for item in inspect(bind).get_indexes("yolo_preannotate_job")}
    if "ix_yolo_preannotate_job_lease_expires_at" not in indexes:
        op.create_index(
            "ix_yolo_preannotate_job_lease_expires_at",
            "yolo_preannotate_job",
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    raise RuntimeError("手工 YOLO 预标注 Worker 字段为前向兼容结构，生产恢复使用备份")
