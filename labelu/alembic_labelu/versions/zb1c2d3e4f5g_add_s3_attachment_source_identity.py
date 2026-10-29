"""Add source identity fields for imported S3 attachments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "zb1c2d3e4f5g"
down_revision = "za0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "task_attachment" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("task_attachment")}
    for name, column in (
        ("source_bucket", sa.Column("source_bucket", sa.String(128), nullable=True)),
        ("source_object_key", sa.Column("source_object_key", sa.String(1024), nullable=True)),
        ("source_version_id", sa.Column("source_version_id", sa.String(256), nullable=True)),
        ("source_etag", sa.Column("source_etag", sa.String(256), nullable=True)),
        ("source_sha256", sa.Column("source_sha256", sa.String(64), nullable=True)),
    ):
        if name not in columns:
            op.add_column("task_attachment", column)

    indexes = {item.get("name") or "" for item in inspect(bind).get_indexes("task_attachment")}
    if "idx_attachment_task_source_identity" not in indexes:
        if bind.dialect.name == "mysql":
            op.create_index(
                "idx_attachment_task_source_identity",
                "task_attachment",
                ["task_id", "source_bucket", "source_object_key", "deleted_at"],
                unique=False,
                mysql_length={"source_object_key": 255},
            )
        else:
            op.create_index(
                "idx_attachment_task_source_identity",
                "task_attachment",
                ["task_id", "source_bucket", "source_object_key", "deleted_at"],
                unique=False,
            )
    if "ix_task_attachment_source_bucket" not in indexes:
        op.create_index(
            "ix_task_attachment_source_bucket",
            "task_attachment",
            ["source_bucket"],
            unique=False,
        )
    if "ix_task_attachment_source_sha256" not in indexes:
        op.create_index(
            "ix_task_attachment_source_sha256",
            "task_attachment",
            ["source_sha256"],
            unique=False,
        )


def downgrade() -> None:
    raise RuntimeError("S3 附件来源身份字段为前向兼容结构，生产恢复使用备份")

