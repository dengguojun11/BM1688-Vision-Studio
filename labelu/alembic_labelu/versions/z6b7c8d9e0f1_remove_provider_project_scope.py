"""Move Provider jobs and prediction review data to creator/task scope.

Project columns remain nullable compatibility storage.  The API and execution
path use creator ownership and task visibility; migration refuses duplicate
creator/request signatures before changing the unique constraint.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "z6b7c8d9e0f1"
down_revision = "z5a6b7c8d9e0"
branch_labels = None
depends_on = None


def _inspector():
    return inspect(op.get_bind())


def _tables():
    return set(_inspector().get_table_names())


def _columns(table):
    return {item["name"] for item in _inspector().get_columns(table)}


def _indexes(table):
    return {item.get("name") or "" for item in _inspector().get_indexes(table)}


def _uniques(table):
    return {item.get("name") or "" for item in _inspector().get_unique_constraints(table)}


def _check_duplicates(table, columns, label):
    bind = op.get_bind()
    if not set(columns) <= _columns(table):
        return
    fields = ", ".join(columns)
    row = bind.execute(
        text(
            f"SELECT {fields}, COUNT(*) AS duplicate_count FROM {table} "
            f"GROUP BY {fields} HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).fetchone()
    if row:
        raise RuntimeError(f"{label} 存在重复数据，迁移失败关闭：{row}")


def _nullable(table, column, unique_old=None, unique_new=None, unique_columns=None):
    bind = op.get_bind()
    if column not in _columns(table):
        return
    uniques = _uniques(table)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            if unique_old and unique_old in uniques:
                batch.drop_constraint(unique_old, type_="unique")
            batch.alter_column(column, existing_type=sa.Integer(), nullable=True)
            if unique_new and unique_new not in uniques:
                batch.create_unique_constraint(unique_new, list(unique_columns))
        return
    if unique_old and unique_old in uniques:
        op.drop_constraint(unique_old, table_name=table, type_="unique")
    op.alter_column(table, column, existing_type=sa.Integer(), nullable=True)
    if unique_new and unique_new not in _uniques(table):
        op.create_unique_constraint(unique_new, table, list(unique_columns))


def _make_nullable(table, column):
    if column not in _columns(table):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.alter_column(column, existing_type=sa.Integer(), nullable=True)
    else:
        op.alter_column(table, column, existing_type=sa.Integer(), nullable=True)


def upgrade():
    tables = _tables()
    if "provider_job" in tables:
        _check_duplicates(
            "provider_job", ("created_by", "request_signature"), "provider_job 创建者请求签名"
        )
        _nullable(
            "provider_job", "project_id", "uq_provider_job_project_request",
            "uq_provider_job_creator_request", ("created_by", "request_signature"),
        )
        if "idx_provider_job_project_status" in _indexes("provider_job"):
            op.drop_index("idx_provider_job_project_status", table_name="provider_job")
        if "idx_provider_job_creator_status" not in _indexes("provider_job"):
            op.create_index(
                "idx_provider_job_creator_status", "provider_job",
                ["created_by", "status", "updated_at"], unique=False,
            )
    if "prediction_set" in tables:
        if "idx_prediction_set_project_review" in _indexes("prediction_set"):
            op.drop_index("idx_prediction_set_project_review", table_name="prediction_set")
        if "idx_prediction_set_creator_review" not in _indexes("prediction_set"):
            op.create_index(
                "idx_prediction_set_creator_review", "prediction_set",
                ["created_by", "review_status", "created_at"], unique=False,
            )
        if "project_id" in _columns("prediction_set"):
            _make_nullable("prediction_set", "project_id")
    if "prediction_set_review_audit" in tables:
        if "idx_prediction_set_review_audit_project_time" in _indexes("prediction_set_review_audit"):
            op.drop_index(
                "idx_prediction_set_review_audit_project_time",
                table_name="prediction_set_review_audit",
            )
        if "project_id" in _columns("prediction_set_review_audit"):
            _make_nullable("prediction_set_review_audit", "project_id")
    if "prediction_set_projection" in tables:
        if "idx_prediction_set_projection_project_time" in _indexes("prediction_set_projection"):
            op.drop_index(
                "idx_prediction_set_projection_project_time",
                table_name="prediction_set_projection",
            )
        if "project_id" in _columns("prediction_set_projection"):
            _make_nullable("prediction_set_projection", "project_id")


def downgrade():
    raise RuntimeError(
        "Provider 去项目化迁移只做前向兼容；生产恢复必须使用迁移前备份"
    )
