"""Add durable Worker fencing for third-party S3 dataset imports."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "z7a8b9c0d1e2"
down_revision = "z6b7c8d9e0f1"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {item["name"] for item in inspect(op.get_bind()).get_columns("experiment_dataset_import")}


def _indexes() -> set[str]:
    return {item.get("name") or "" for item in inspect(op.get_bind()).get_indexes("experiment_dataset_import")}


def upgrade() -> None:
    if "experiment_dataset_import" not in inspect(op.get_bind()).get_table_names():
        return
    columns = _columns()
    for name, column in (
        ("worker_lease_owner", sa.Column("worker_lease_owner", sa.String(128), nullable=True)),
        ("worker_lease_expires_at", sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True)),
        ("worker_fencing_token", sa.Column("worker_fencing_token", sa.Integer(), nullable=False, server_default="0")),
        ("attempt_count", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")),
    ):
        if name not in columns:
            op.add_column("experiment_dataset_import", column)
    if "idx_experiment_dataset_import_worker_scan" not in _indexes():
        op.create_index(
            "idx_experiment_dataset_import_worker_scan",
            "experiment_dataset_import",
            ["status", "worker_lease_expires_at", "id"],
            unique=False,
        )
    for name, columns in (
        ("ix_experiment_dataset_import_worker_lease_owner", ["worker_lease_owner"]),
        ("ix_experiment_dataset_import_worker_lease_expires_at", ["worker_lease_expires_at"]),
    ):
        if name not in _indexes():
            op.create_index(name, "experiment_dataset_import", columns, unique=False)


def downgrade() -> None:
    raise RuntimeError("第三方回流 Worker 租约为前向兼容结构，生产恢复使用备份")

