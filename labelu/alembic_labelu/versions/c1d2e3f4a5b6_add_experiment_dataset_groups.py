"""Add buffered experiment dataset groups and published-model binding state.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql


revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "experiment_dataset_group" not in tables:
        op.create_table(
            "experiment_dataset_group",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("aggregation_key", sa.String(length=512), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=False),
            sa.Column("source_system", sa.String(length=64), nullable=True),
            sa.Column("source_external_task_id", sa.String(length=128), nullable=True),
            sa.Column("dataset_type", sa.String(length=64), nullable=False),
            sa.Column("dataset_date", sa.String(length=32), nullable=False),
            sa.Column("event_type", sa.String(length=128), nullable=False),
            sa.Column("model_id", sa.String(length=128), nullable=False),
            sa.Column(
                "identity_json",
                sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
                nullable=False,
            ),
            sa.Column("min_sample_count", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("declared_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("valid_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="COLLECTING"),
            sa.Column("formal_task_id", sa.Integer(), nullable=True),
            sa.Column(
                "model_resolution_json",
                sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
                nullable=True,
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("ready_at", sa.DateTime(), nullable=True),
            sa.Column("created_task_at", sa.DateTime(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["formal_task_id"], ["task.id"]),
            sa.UniqueConstraint("aggregation_key", name="uq_experiment_dataset_group_key"),
        )
        op.create_index(
            "ix_experiment_dataset_group_status",
            "experiment_dataset_group",
            ["status"],
        )

    if "experiment_dataset_import" not in tables:
        return
    columns = {item["name"] for item in inspect(bind).get_columns("experiment_dataset_import")}
    if "aggregation_key" not in columns:
        op.add_column(
            "experiment_dataset_import",
            sa.Column("aggregation_key", sa.String(length=512), nullable=True),
        )
        op.create_index(
            "ix_experiment_dataset_import_aggregation_key",
            "experiment_dataset_import",
            ["aggregation_key"],
        )
    if "aggregation_group_id" not in columns:
        op.add_column(
            "experiment_dataset_import",
            sa.Column("aggregation_group_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_experiment_dataset_import_group",
            "experiment_dataset_import",
            "experiment_dataset_group",
            ["aggregation_group_id"],
            ["id"],
        )
        op.create_index(
            "ix_experiment_dataset_import_aggregation_group_id",
            "experiment_dataset_import",
            ["aggregation_group_id"],
        )
    if "valid_sample_count" not in columns:
        op.add_column(
            "experiment_dataset_import",
            sa.Column("valid_sample_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "experiment_dataset_import" in tables:
        columns = {item["name"] for item in inspect(bind).get_columns("experiment_dataset_import")}
        if "aggregation_group_id" in columns:
            op.drop_index(
                "ix_experiment_dataset_import_aggregation_group_id",
                table_name="experiment_dataset_import",
            )
            op.drop_constraint(
                "fk_experiment_dataset_import_group",
                "experiment_dataset_import",
                type_="foreignkey",
            )
            op.drop_column("experiment_dataset_import", "aggregation_group_id")
        if "aggregation_key" in columns:
            op.drop_index(
                "ix_experiment_dataset_import_aggregation_key",
                table_name="experiment_dataset_import",
            )
            op.drop_column("experiment_dataset_import", "aggregation_key")
        if "valid_sample_count" in columns:
            op.drop_column("experiment_dataset_import", "valid_sample_count")
    if "experiment_dataset_group" in tables:
        op.drop_index("ix_experiment_dataset_group_status", table_name="experiment_dataset_group")
        op.drop_table("experiment_dataset_group")
