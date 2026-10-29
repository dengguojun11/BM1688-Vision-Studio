"""Add dispatcher/execution leases and BM1688 worker action routing.

This is the single forward migration for the execution fencing contract used
by training, BM1688 compilation, and edge validation workers.  It is deliberately
idempotent because some deployments already contain a subset of the columns
from historical hotfixes.  The production rollback procedure is backup restore;
model publication is never rolled back by this migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "z4a5b6c7d8e9"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


_TABLES = {
    "yolo_train_job": {
        "columns": {
            "queue_lease_owner": sa.Column(
                "queue_lease_owner", sa.String(128), nullable=True
            ),
            "queue_lease_expires_at": sa.Column(
                "queue_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            "execution_lease_owner": sa.Column(
                "execution_lease_owner", sa.String(128), nullable=True
            ),
            "execution_lease_expires_at": sa.Column(
                "execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            "execution_fencing_token": sa.Column(
                "execution_fencing_token",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            "assigned_gpu_json": sa.Column(
                "assigned_gpu_json", sa.Text(), nullable=True
            ),
            "execution_runtime_pid": sa.Column(
                "execution_runtime_pid", sa.Integer(), nullable=True
            ),
        },
        "indexes": {
            "ix_yolo_train_job_queue_lease_owner": ["queue_lease_owner"],
            "ix_yolo_train_job_queue_lease_expires_at": ["queue_lease_expires_at"],
            "ix_yolo_train_job_execution_lease_owner": ["execution_lease_owner"],
            "ix_yolo_train_job_execution_lease_expires_at": [
                "execution_lease_expires_at"
            ],
            "ix_yolo_train_job_training_worker_scan": [
                "status",
                "queue_lease_expires_at",
                "id",
            ],
        },
    },
    "yolo_bm1688_export": {
        "columns": {
            "queue_lease_owner": sa.Column(
                "queue_lease_owner", sa.String(128), nullable=True
            ),
            "queue_lease_expires_at": sa.Column(
                "queue_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            "execution_lease_owner": sa.Column(
                "execution_lease_owner", sa.String(128), nullable=True
            ),
            "execution_lease_expires_at": sa.Column(
                "execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            "execution_fencing_token": sa.Column(
                "execution_fencing_token",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            "worker_action": sa.Column("worker_action", sa.String(32), nullable=True),
        },
        "indexes": {
            "ix_yolo_bm1688_export_queue_lease_owner": ["queue_lease_owner"],
            "ix_yolo_bm1688_export_queue_lease_expires_at": ["queue_lease_expires_at"],
            "ix_yolo_bm1688_export_execution_lease_owner": ["execution_lease_owner"],
            "ix_yolo_bm1688_export_execution_lease_expires_at": [
                "execution_lease_expires_at"
            ],
            "ix_yolo_bm1688_export_worker_action": ["worker_action"],
            "ix_yolo_bm1688_export_worker_scan": [
                "status",
                "worker_action",
                "queue_lease_expires_at",
                "id",
            ],
        },
    },
    "yolo_edge_validation_job": {
        "columns": {
            "queue_lease_owner": sa.Column(
                "queue_lease_owner", sa.String(128), nullable=True
            ),
            "queue_lease_expires_at": sa.Column(
                "queue_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            "execution_lease_owner": sa.Column(
                "execution_lease_owner", sa.String(128), nullable=True
            ),
            "execution_lease_expires_at": sa.Column(
                "execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            "execution_fencing_token": sa.Column(
                "execution_fencing_token",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        },
        "indexes": {
            "ix_yolo_edge_validation_job_queue_lease_owner": ["queue_lease_owner"],
            "ix_yolo_edge_validation_job_queue_lease_expires_at": [
                "queue_lease_expires_at"
            ],
            "ix_yolo_edge_validation_job_execution_lease_owner": [
                "execution_lease_owner"
            ],
            "ix_yolo_edge_validation_job_execution_lease_expires_at": [
                "execution_lease_expires_at"
            ],
            "ix_yolo_edge_validation_job_dispatch_scan": ["status", "queued_at", "id"],
        },
    },
}


def _inspector():
    return inspect(op.get_bind())


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in _inspector().get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {item.get("name") or "" for item in _inspector().get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    table_names = set(_inspector().get_table_names())
    missing = sorted(set(_TABLES) - table_names)
    if missing:
        raise RuntimeError(
            "执行租约正式迁移要求前置版本已创建目标表：" + ", ".join(missing)
        )

    for table_name, contract in _TABLES.items():
        existing_columns = _columns(table_name)
        for column_name, column in contract["columns"].items():
            if column_name not in existing_columns:
                op.add_column(table_name, column)

        existing_indexes = _indexes(table_name)
        for index_name, columns in contract["indexes"].items():
            if index_name not in existing_indexes:
                op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    raise RuntimeError(
        "执行租约迁移为前向兼容结构变更；生产恢复必须使用迁移前备份，"
        "禁止自动删列或删索引以免误删历史热修字段"
    )
