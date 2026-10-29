"""link active-learning batches to generated dataset versions

Revision ID: w6x7y8z9a0b
Revises: v5w6x7y8z9a0
"""

from alembic import op
import sqlalchemy as sa


revision = "w6x7y8z9a0b"
down_revision = "v5w6x7y8z9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "active_learning_batch" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("active_learning_batch")}
    if "dataset_version_id" not in columns:
        op.add_column(
            "active_learning_batch",
            sa.Column("dataset_version_id", sa.Integer(), nullable=True),
        )
    indexes = {index["name"] for index in inspector.get_indexes("active_learning_batch")}
    if "ix_active_learning_batch_dataset_version_id" not in indexes:
        op.create_index(
            "ix_active_learning_batch_dataset_version_id",
            "active_learning_batch",
            ["dataset_version_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "active_learning_batch" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("active_learning_batch")}
    if "ix_active_learning_batch_dataset_version_id" in indexes:
        op.drop_index(
            "ix_active_learning_batch_dataset_version_id",
            table_name="active_learning_batch",
        )
    columns = {column["name"] for column in inspector.get_columns("active_learning_batch")}
    if "dataset_version_id" in columns:
        op.drop_column("active_learning_batch", "dataset_version_id")

