"""一期候选迁移：异步评估执行代次与报告幂等键。

本文件故意放在 ``docs/7gai_phase_one_migration_candidates``，不属于正式
Alembic 版本链。正式上线前必须在同版本 MySQL 沙箱完成首次/重复升级、旧
报告查询、重复键、锁等待、备份恢复和并发演练，再由架构和运维将等价内容
生成到正式 head。没有这些证据时不得执行本文件或直接修改生产数据库。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "gai_p1_09"
down_revision = "z9f3a4b5c6d7"
branch_labels = None
depends_on = None


_JOB_COLUMNS = (
    "ai_eval_full_operation_id",
    "ai_eval_next_operation_id",
)
_JOB_INDEXES = (
    "ix_yolo_train_job_ai_eval_full_operation_id",
    "ix_yolo_train_job_ai_eval_next_operation_id",
)
_REPORT_COLUMNS = ("operation_id",)
_REPORT_INDEX = "ix_yolo_eval_report_operation_id"
_REPORT_UNIQUE_INDEX = "uq_yolo_eval_report_operation"


def _table_names(bind) -> set[str]:
    return {str(name) for name in inspect(bind).get_table_names()}


def _columns(bind, table_name: str) -> set[str]:
    return {str(item["name"]) for item in inspect(bind).get_columns(table_name)}


def _indexes(bind, table_name: str) -> set[str]:
    return {str(item.get("name") or "") for item in inspect(bind).get_indexes(table_name)}


def _assert_no_duplicate_generations(bind) -> None:
    duplicate_rows = bind.execute(
        text(
            """
            SELECT yolo_train_job_id, report_kind, provider_key, operation_id, COUNT(*) AS n
            FROM yolo_eval_report
            WHERE operation_id IS NOT NULL
            GROUP BY yolo_train_job_id, report_kind, provider_key, operation_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicate_rows:
        raise RuntimeError(
            "yolo_eval_report 存在重复执行代次，拒绝创建唯一索引；"
            "请先导出并人工处理重复报告。"
        )


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    required = {"yolo_train_job", "yolo_eval_report"}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError("评估代次候选迁移缺少目标表：" + ", ".join(missing))

    job_columns = _columns(bind, "yolo_train_job")
    for column_name in _JOB_COLUMNS:
        if column_name not in job_columns:
            op.add_column(
                "yolo_train_job",
                sa.Column(column_name, sa.String(length=64), nullable=True),
            )

    job_indexes = _indexes(bind, "yolo_train_job")
    for index_name, column_name in zip(_JOB_INDEXES, _JOB_COLUMNS):
        if index_name not in job_indexes:
            op.create_index(index_name, "yolo_train_job", [column_name], unique=False)

    report_columns = _columns(bind, "yolo_eval_report")
    if "operation_id" not in report_columns:
        op.add_column(
            "yolo_eval_report",
            sa.Column("operation_id", sa.String(length=64), nullable=True),
        )

    _assert_no_duplicate_generations(bind)
    report_indexes = _indexes(bind, "yolo_eval_report")
    if _REPORT_INDEX not in report_indexes:
        op.create_index(_REPORT_INDEX, "yolo_eval_report", ["operation_id"], unique=False)
    if _REPORT_UNIQUE_INDEX not in report_indexes:
        op.create_index(
            _REPORT_UNIQUE_INDEX,
            "yolo_eval_report",
            ["yolo_train_job_id", "report_kind", "provider_key", "operation_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "yolo_eval_report" in tables:
        indexes = _indexes(bind, "yolo_eval_report")
        for index_name in (_REPORT_UNIQUE_INDEX, _REPORT_INDEX):
            if index_name in indexes:
                op.drop_index(index_name, table_name="yolo_eval_report")
        columns = _columns(bind, "yolo_eval_report")
        if "operation_id" in columns:
            op.drop_column("yolo_eval_report", "operation_id")
    if "yolo_train_job" in tables:
        indexes = _indexes(bind, "yolo_train_job")
        for index_name in reversed(_JOB_INDEXES):
            if index_name in indexes:
                op.drop_index(index_name, table_name="yolo_train_job")
        columns = _columns(bind, "yolo_train_job")
        for column_name in reversed(_JOB_COLUMNS):
            if column_name in columns:
                op.drop_column("yolo_train_job", column_name)
