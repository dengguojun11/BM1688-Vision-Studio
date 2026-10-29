"""Add edge validation device scheduling fields.

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
Create Date: 2026-07-31 10:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t2u3v4w5x6y7"
down_revision = "s1t2u3v4w5x6"
branch_labels = None
depends_on = None


_COLUMNS = (
    sa.Column("edge_device_id", sa.String(length=64), nullable=True),
    sa.Column("edge_device_name", sa.String(length=64), nullable=True),
    sa.Column("edge_host", sa.String(length=256), nullable=True),
    sa.Column("edge_port", sa.Integer(), nullable=True),
    sa.Column("edge_user", sa.String(length=128), nullable=True),
    sa.Column("queue_reason", sa.String(length=64), nullable=True),
    sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
)


def _index_names(inspector) -> set[str]:
    return {
        str(item.get("name") or "")
        for item in inspector.get_indexes("yolo_edge_validation_job")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    existing = {
        item["name"] for item in inspector.get_columns("yolo_edge_validation_job")
    }
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("yolo_edge_validation_job", column)

    inspector = inspect(bind)
    indexes = _index_names(inspector)
    if "ix_yolo_edge_validation_job_edge_device_id" not in indexes:
        op.create_index(
            "ix_yolo_edge_validation_job_edge_device_id",
            "yolo_edge_validation_job",
            ["edge_device_id"],
            unique=False,
        )
    if "ix_yolo_edge_validation_job_queued_at" not in indexes:
        op.create_index(
            "ix_yolo_edge_validation_job_queued_at",
            "yolo_edge_validation_job",
            ["queued_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "yolo_edge_validation_job" not in inspector.get_table_names():
        return
    indexes = _index_names(inspector)
    for index_name in (
        "ix_yolo_edge_validation_job_queued_at",
        "ix_yolo_edge_validation_job_edge_device_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="yolo_edge_validation_job")

    existing = {
        item["name"] for item in inspect(bind).get_columns("yolo_edge_validation_job")
    }
    for column in reversed(_COLUMNS):
        if column.name in existing:
            op.drop_column("yolo_edge_validation_job", column.name)
