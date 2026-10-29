"""Complete model-version lineage columns for databases stamped early.

The model-series migration was previously applied to some databases after only
part of its ``model_version`` changes had landed.  Keep this follow-up
migration idempotent so those databases can be upgraded without data loss.
"""

from alembic import op
import sqlalchemy as sa


revision = "zi5j6k7l8m9"
down_revision = "zh4i5j6k7l8"
branch_labels = None
depends_on = None


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "model_version" not in inspector.get_table_names():
        return

    existing_columns = _columns(bind, "model_version")
    for column in (
        sa.Column("branch_key", sa.String(length=64), nullable=True),
        sa.Column("selection_contract_json", sa.Text(), nullable=True),
        sa.Column("parent_training_job_id", sa.Integer(), nullable=True),
    ):
        if column.name not in existing_columns:
            op.add_column("model_version", column)

    existing_indexes = _indexes(bind, "model_version")
    for name, columns in (
        ("ix_model_version_branch_key", ["branch_key"]),
        ("ix_model_version_parent_training_job_id", ["parent_training_job_id"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "model_version", columns, unique=False)


def downgrade() -> None:
    raise RuntimeError(
        "模型版本血缘字段用于启动恢复和审计，禁止自动回退；请从备份恢复。"
    )
