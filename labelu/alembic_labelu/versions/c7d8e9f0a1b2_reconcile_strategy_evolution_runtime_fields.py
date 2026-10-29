"""reconcile strategy evolution runtime fields

Revision ID: c7d8e9f0a1b2
Revises: b1c2d3e4f5g6
Create Date: 2026-07-15 13:55:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "c7d8e9f0a1b2"
down_revision = "b1c2d3e4f5g6"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {item["name"] for item in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = inspect(bind)
    return {item["name"] for item in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in _column_names(table_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if index_name in _index_names(table_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    table_name = "strategy_task_integration"
    if not _table_exists(table_name):
        return

    _add_column_if_missing(
        table_name, sa.Column("automation_source", sa.String(length=64), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("automation_stage", sa.String(length=64), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("automation_status", sa.String(length=32), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("automation_result_json", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("outbound_callback_url", sa.String(length=1024), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("outbound_callback_status", sa.String(length=32), nullable=True)
    )
    _add_column_if_missing(
        table_name,
        sa.Column(
            "outbound_callback_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    _add_column_if_missing(
        table_name, sa.Column("outbound_callback_last_error", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("source_train_job_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("source_export_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("derived_train_job_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("derived_export_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("derived_edge_validation_job_id", sa.Integer(), nullable=True)
    )

    _create_index_if_missing(
        table_name,
        op.f("ix_strategy_task_integration_source_train_job_id"),
        ["source_train_job_id"],
    )
    _create_index_if_missing(
        table_name,
        op.f("ix_strategy_task_integration_source_export_id"),
        ["source_export_id"],
    )
    _create_index_if_missing(
        table_name,
        op.f("ix_strategy_task_integration_derived_train_job_id"),
        ["derived_train_job_id"],
    )
    _create_index_if_missing(
        table_name,
        op.f("ix_strategy_task_integration_derived_export_id"),
        ["derived_export_id"],
    )
    _create_index_if_missing(
        table_name,
        op.f("ix_strategy_task_integration_derived_edge_validation_job_id"),
        ["derived_edge_validation_job_id"],
    )


def downgrade() -> None:
    table_name = "strategy_task_integration"
    if not _table_exists(table_name):
        return

    for index_name in (
        op.f("ix_strategy_task_integration_derived_edge_validation_job_id"),
        op.f("ix_strategy_task_integration_derived_export_id"),
        op.f("ix_strategy_task_integration_derived_train_job_id"),
        op.f("ix_strategy_task_integration_source_export_id"),
        op.f("ix_strategy_task_integration_source_train_job_id"),
    ):
        _drop_index_if_exists(table_name, index_name)

    for column_name in (
        "derived_edge_validation_job_id",
        "derived_export_id",
        "derived_train_job_id",
        "source_export_id",
        "source_train_job_id",
        "outbound_callback_last_error",
        "outbound_callback_attempts",
        "outbound_callback_status",
        "outbound_callback_url",
        "automation_result_json",
        "automation_status",
        "automation_stage",
        "automation_source",
    ):
        _drop_column_if_exists(table_name, column_name)
