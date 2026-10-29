"""一期候选迁移：BM1688/边端调度与执行期租约。

本文件只存放在方案候选目录，不属于正式 Alembic versions。其字段和索引
已经由正式 ``z4a5b6c7d8e9`` 迁移覆盖，本文件保留用于历史对账和重建审计，
不得再次复制到正式目录或作为待执行迁移。

``queue_lease_*`` 只负责调度器交接；``execution_lease_*`` 和
``execution_fencing_token`` 负责编译/边端 worker 的实际写入 fencing。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "gai_p1_08"
down_revision = "gai_p1_07"
branch_labels = None
depends_on = None

# This draft was superseded by the formal linear migration below. Keep the
# file for audit/rebuild history, but never treat it as a pending migration.
SUPERSEDED_BY = "z4a5b6c7d8e9"


_TABLES = {
    "yolo_train_job": (
        "ix_yolo_train_job_queue_lease_owner",
        "ix_yolo_train_job_queue_lease_expires_at",
        "ix_yolo_train_job_execution_lease_owner",
        "ix_yolo_train_job_execution_lease_expires_at",
        "ix_yolo_train_job_training_worker_scan",
    ),
    "yolo_bm1688_export": (
        "ix_yolo_bm1688_export_queue_lease_owner",
        "ix_yolo_bm1688_export_queue_lease_expires_at",
        "ix_yolo_bm1688_export_execution_lease_owner",
        "ix_yolo_bm1688_export_execution_lease_expires_at",
        "ix_yolo_bm1688_export_worker_scan",
    ),
    "yolo_edge_validation_job": (
        "ix_yolo_edge_validation_job_queue_lease_owner",
        "ix_yolo_edge_validation_job_queue_lease_expires_at",
        "ix_yolo_edge_validation_job_execution_lease_owner",
        "ix_yolo_edge_validation_job_execution_lease_expires_at",
        "ix_yolo_edge_validation_job_dispatch_scan",
    ),
}


def _columns(bind, table_name: str) -> set[str]:
    return {
        str(item["name"])
        for item in inspect(bind).get_columns(table_name)
    }


def _indexes(bind, table_name: str) -> set[str]:
    return {
        str(item.get("name") or "")
        for item in inspect(bind).get_indexes(table_name)
    }


def upgrade() -> None:
    bind = op.get_bind()
    table_names = set(inspect(bind).get_table_names())
    required_tables = set(_TABLES)
    missing_tables = sorted(required_tables - table_names)
    if missing_tables:
        raise RuntimeError(
            "一期租约候选迁移要求前置段创建目标表："
            + ", ".join(missing_tables)
        )

    for table_name in _TABLES:
        existing = _columns(bind, table_name)
        if "queue_lease_owner" not in existing:
            op.add_column(
                table_name,
                sa.Column("queue_lease_owner", sa.String(length=128), nullable=True),
            )
        if "queue_lease_expires_at" not in existing:
            op.add_column(
                table_name,
                sa.Column(
                    "queue_lease_expires_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
            )
        if "execution_lease_owner" not in existing:
            op.add_column(
                table_name,
                sa.Column("execution_lease_owner", sa.String(length=128), nullable=True),
            )
        if "execution_lease_expires_at" not in existing:
            op.add_column(
                table_name,
                sa.Column(
                    "execution_lease_expires_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
            )
        if "execution_fencing_token" not in existing:
            op.add_column(
                table_name,
                sa.Column(
                    "execution_fencing_token",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )
        if table_name == "yolo_bm1688_export" and "worker_action" not in existing:
            op.add_column(
                table_name,
                sa.Column("worker_action", sa.String(length=32), nullable=True),
            )
        if table_name == "yolo_train_job":
            if "assigned_gpu_json" not in existing:
                op.add_column(
                    table_name,
                    sa.Column("assigned_gpu_json", sa.Text(), nullable=True),
                )
            if "execution_runtime_pid" not in existing:
                op.add_column(
                    table_name,
                    sa.Column("execution_runtime_pid", sa.Integer(), nullable=True),
                )

        existing_indexes = _indexes(bind, table_name)
        (
            queue_owner_index,
            queue_expiry_index,
            execution_owner_index,
            execution_expiry_index,
            worker_scan_index,
        ) = _TABLES[table_name]
        if queue_owner_index not in existing_indexes:
            op.create_index(queue_owner_index, table_name, ["queue_lease_owner"], unique=False)
        if queue_expiry_index not in existing_indexes:
            op.create_index(
                queue_expiry_index,
                table_name,
                ["queue_lease_expires_at"],
                unique=False,
            )
        if execution_owner_index not in existing_indexes:
            op.create_index(
                execution_owner_index,
                table_name,
                ["execution_lease_owner"],
                unique=False,
            )
        if execution_expiry_index not in existing_indexes:
            op.create_index(
                execution_expiry_index,
                table_name,
                ["execution_lease_expires_at"],
                unique=False,
            )
        worker_action_index = "ix_yolo_bm1688_export_worker_action"
        if table_name == "yolo_bm1688_export" and worker_action_index not in existing_indexes:
            op.create_index(worker_action_index, table_name, ["worker_action"], unique=False)
        if worker_scan_index not in existing_indexes:
            worker_scan_columns = (
                ["status", "worker_action", "queue_lease_expires_at", "id"]
                if table_name == "yolo_bm1688_export"
                else ["status", "queued_at", "id"]
                if table_name == "yolo_edge_validation_job"
                else ["status", "queue_lease_expires_at", "id"]
            )
            op.create_index(
                worker_scan_index,
                table_name,
                worker_scan_columns,
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    table_names = set(inspect(bind).get_table_names())
    for table_name, indexes in _TABLES.items():
        if table_name not in table_names:
            continue
        existing_indexes = _indexes(bind, table_name)
        index_names = list(indexes)
        if table_name == "yolo_bm1688_export":
            index_names.append("ix_yolo_bm1688_export_worker_action")
        for index_name in reversed(index_names):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=table_name)
        existing = _columns(bind, table_name)
        for column_name in (
            "execution_runtime_pid",
            "assigned_gpu_json",
            "execution_fencing_token",
            "execution_lease_expires_at",
            "execution_lease_owner",
            "queue_lease_expires_at",
            "queue_lease_owner",
        ):
            if column_name in existing:
                op.drop_column(table_name, column_name)
        if table_name == "yolo_bm1688_export" and "worker_action" in existing:
            op.drop_column(table_name, "worker_action")
