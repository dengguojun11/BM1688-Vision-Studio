"""Add cross-process AICM publication claim/fencing fields.

Publication is an external side effect, so the export row keeps a durable
claim separate from compiler and edge-execution leases.  The migration is
idempotent for environments that received one or more historical hotfixes.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "z9b0c1d2e3f4"
down_revision = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


_COLUMNS = {
    "publish_status": sa.Column("publish_status", sa.String(32), nullable=True),
    "publish_job_id": sa.Column("publish_job_id", sa.String(64), nullable=True),
    "publish_lease_owner": sa.Column(
        "publish_lease_owner", sa.String(128), nullable=True
    ),
    "publish_lease_expires_at": sa.Column(
        "publish_lease_expires_at", sa.DateTime(timezone=True), nullable=True
    ),
    "publish_fencing_token": sa.Column(
        "publish_fencing_token", sa.Integer(), nullable=False, server_default="0"
    ),
}

_INDEXES = {
    "ix_yolo_bm1688_export_publish_status": ["publish_status"],
    "ix_yolo_bm1688_export_publish_job_id": ["publish_job_id"],
    "ix_yolo_bm1688_export_publish_lease_owner": ["publish_lease_owner"],
    "ix_yolo_bm1688_export_publish_lease_expires_at": [
        "publish_lease_expires_at"
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "yolo_bm1688_export" not in tables:
        raise RuntimeError("AICM 发布租约迁移要求 yolo_bm1688_export 已存在")
    columns = {item["name"] for item in inspector.get_columns("yolo_bm1688_export")}
    for name, column in _COLUMNS.items():
        if name not in columns:
            op.add_column("yolo_bm1688_export", column)
    indexes = {item.get("name") or "" for item in inspector.get_indexes("yolo_bm1688_export")}
    for name, column_names in _INDEXES.items():
        if name not in indexes:
            op.create_index(name, "yolo_bm1688_export", column_names, unique=False)


def downgrade() -> None:
    raise RuntimeError(
        "AICM 发布租约为外部副作用保护字段，生产降级必须恢复迁移前备份，禁止自动删列"
    )
