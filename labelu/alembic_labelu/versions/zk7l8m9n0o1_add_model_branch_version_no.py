"""Add a branch-local model version sequence.

The original model_version.version_no is global per algorithm and remains in
place for backwards-compatible model keys and integrations.  New lineage rows
also receive branch_version_no, which is scoped by series_id/variant_id.
"""

from alembic import op
import sqlalchemy as sa


revision = "zk7l8m9n0o1"
down_revision = "zj6k7l8m9n0"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "model_version" not in _tables(bind):
        return
    if "branch_version_no" not in _columns(bind, "model_version"):
        op.add_column(
            "model_version",
            sa.Column("branch_version_no", sa.Integer(), nullable=True),
        )
    index_name = "idx_model_version_branch_version"
    if index_name not in _indexes(bind, "model_version"):
        op.create_index(
            index_name,
            "model_version",
            ["series_id", "variant_id", "branch_version_no"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "model_version" not in _tables(bind):
        return
    index_name = "idx_model_version_branch_version"
    if index_name in _indexes(bind, "model_version"):
        op.drop_index(index_name, table_name="model_version")
    if "branch_version_no" in _columns(bind, "model_version"):
        op.drop_column("model_version", "branch_version_no")

