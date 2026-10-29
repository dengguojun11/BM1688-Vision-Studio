"""add strategy evolution platform fields

Revision ID: b1c2d3e4f5g6
Revises: b1c2d3e4f5a
Create Date: 2026-07-13 14:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "b1c2d3e4f5g6"
down_revision = "b1c2d3e4f5a"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(table_name)}
    if column.name not in columns:
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(table_name)}
    if column_name in columns:
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    table_name = "strategy_task_integration"
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return

    _add_column_if_missing(
        table_name, sa.Column("data_window_json", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        table_name,
        sa.Column(
            "platform_status",
            sa.String(length=32),
            nullable=False,
            server_default="accepted",
        ),
    )
    _add_column_if_missing(
        table_name, sa.Column("platform_progress_json", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("platform_result_json", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        table_name, sa.Column("platform_error_json", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        table_name,
        sa.Column("platform_callback_event_id", sa.String(length=128), nullable=True),
    )
    _add_column_if_missing(
        table_name,
        sa.Column("platform_finished_at", sa.String(length=64), nullable=True),
    )
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


def downgrade() -> None:
    table_name = "strategy_task_integration"
    for column_name in (
        "platform_finished_at",
        "platform_callback_event_id",
        "platform_error_json",
        "platform_result_json",
        "platform_progress_json",
        "platform_status",
        "data_window_json",
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
