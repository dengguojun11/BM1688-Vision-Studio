"""Bind canonical data-center datasets to the existing taxonomy categories."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "z0a1b2c3d4f6"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
    if column.name not in columns:
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(
        bind,
        "dataset",
        sa.Column("dataset_category_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "dataset_version",
        sa.Column("dataset_category_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "dataset_version",
        sa.Column("dataset_category_snapshot_json", sa.Text(), nullable=True),
    )
    indexes = {item["name"] for item in inspect(bind).get_indexes("dataset")}
    if "ix_dataset_dataset_category_id" not in indexes:
        op.create_index("ix_dataset_dataset_category_id", "dataset", ["dataset_category_id"])
    indexes = {item["name"] for item in inspect(bind).get_indexes("dataset_version")}
    if "ix_dataset_version_dataset_category_id" not in indexes:
        op.create_index(
            "ix_dataset_version_dataset_category_id",
            "dataset_version",
            ["dataset_category_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    for index_name, table_name in (
        ("ix_dataset_version_dataset_category_id", "dataset_version"),
        ("ix_dataset_dataset_category_id", "dataset"),
    ):
        if index_name in {item["name"] for item in inspect(bind).get_indexes(table_name)}:
            op.drop_index(index_name, table_name=table_name)
    for table_name, column_names in (
        ("dataset_version", ("dataset_category_snapshot_json", "dataset_category_id")),
        ("dataset", ("dataset_category_id",)),
    ):
        existing = {item["name"] for item in inspect(bind).get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch:
            for column_name in column_names:
                if column_name in existing:
                    batch.drop_column(column_name)
