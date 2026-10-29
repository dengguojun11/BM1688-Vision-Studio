"""Persist provider preannotation inputs and worker leases."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "ze1f2a3b4c5d"
down_revision = "zd1e2f3a4b5c"
branch_labels = None
depends_on = None


def _add_columns(table: str, definitions: list[tuple[str, sa.Column]]) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns(table)}
    for name, column in definitions:
        if name not in columns:
            op.add_column(table, column)
            columns.add(name)


def upgrade() -> None:
    _add_columns(
        "tagtool_preannotate_job",
        [
            ("payload_json", sa.Column("payload_json", sa.Text(), nullable=True)),
            ("attempt_count", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")),
            ("lease_owner", sa.Column("lease_owner", sa.String(128), nullable=True)),
            ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)),
            ("lease_fencing_token", sa.Column("lease_fencing_token", sa.Integer(), nullable=False, server_default="0")),
        ],
    )
    _add_columns(
        "locateanything_preannotate_job",
        [
            ("provider", sa.Column("provider", sa.String(32), nullable=False, server_default="locateanything")),
            ("payload_json", sa.Column("payload_json", sa.Text(), nullable=True)),
            ("attempt_count", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")),
            ("lease_owner", sa.Column("lease_owner", sa.String(128), nullable=True)),
            ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)),
            ("lease_fencing_token", sa.Column("lease_fencing_token", sa.Integer(), nullable=False, server_default="0")),
        ],
    )
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, name in (
        ("tagtool_preannotate_job", "ix_tagtool_preannotate_job_lease_expires_at"),
        ("locateanything_preannotate_job", "ix_locateanything_preannotate_job_lease_expires_at"),
    ):
        if table in inspector.get_table_names() and name not in {item.get("name") for item in inspect(bind).get_indexes(table)}:
            op.create_index(name, table, ["lease_expires_at"], unique=False)
    if "locateanything_preannotate_job" in inspector.get_table_names():
        columns = {item["name"] for item in inspect(bind).get_columns("locateanything_preannotate_job")}
        if "provider" in columns:
            indexes = {item.get("name") for item in inspect(bind).get_indexes("locateanything_preannotate_job")}
            if "ix_locateanything_preannotate_job_provider" not in indexes:
                op.create_index("ix_locateanything_preannotate_job_provider", "locateanything_preannotate_job", ["provider"], unique=False)


def downgrade() -> None:
    raise RuntimeError("Provider 预标注 Worker 字段为前向兼容结构，生产恢复使用备份")
