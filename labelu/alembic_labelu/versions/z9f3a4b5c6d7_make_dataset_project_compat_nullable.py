"""Keep historical dataset project links while allowing global evolution datasets."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "z9f3a4b5c6d7"
down_revision = "z9e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    for table in ("dataset", "dataset_version"):
        if table not in tables:
            continue
        columns = {item["name"] for item in inspect(bind).get_columns(table)}
        if "project_id" not in columns:
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column("project_id", existing_type=sa.Integer(), nullable=True)
        else:
            op.alter_column(table, "project_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    raise RuntimeError("数据集全局兼容迁移不可自动降级，生产需从备份恢复")
