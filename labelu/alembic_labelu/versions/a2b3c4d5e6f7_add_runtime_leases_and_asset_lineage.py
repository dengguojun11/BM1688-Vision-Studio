"""Add strategy runtime leases and model asset lineage.

Revision ID: a2b3c4d5e6f7
Revises: z1y2x3w4v5u6
"""

from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "z1y2x3w4v5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategy_task_integration",
        sa.Column("automation_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "strategy_task_integration",
        sa.Column("automation_lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "strategy_task_integration",
        sa.Column("automation_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "strategy_task_integration",
        sa.Column("outbound_callback_event_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "strategy_task_integration",
        sa.Column("outbound_callback_next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_strategy_task_integration_automation_lease_expires_at"),
        "strategy_task_integration",
        ["automation_lease_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_outbound_callback_next_retry_at"),
        "strategy_task_integration",
        ["outbound_callback_next_retry_at"],
        unique=False,
    )
    op.add_column("yolo_train_job", sa.Column("dataset_manifest_json", sa.Text(), nullable=True))
    op.add_column("yolo_bm1688_export", sa.Column("artifact_manifest_json", sa.Text(), nullable=True))
    op.add_column("yolo_bm1688_export", sa.Column("quality_gate_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("yolo_bm1688_export", "quality_gate_json")
    op.drop_column("yolo_bm1688_export", "artifact_manifest_json")
    op.drop_column("yolo_train_job", "dataset_manifest_json")
    op.drop_index(
        op.f("ix_strategy_task_integration_outbound_callback_next_retry_at"),
        table_name="strategy_task_integration",
    )
    op.drop_index(
        op.f("ix_strategy_task_integration_automation_lease_expires_at"),
        table_name="strategy_task_integration",
    )
    op.drop_column("strategy_task_integration", "outbound_callback_next_retry_at")
    op.drop_column("strategy_task_integration", "outbound_callback_event_id")
    op.drop_column("strategy_task_integration", "automation_lease_expires_at")
    op.drop_column("strategy_task_integration", "automation_lease_owner")
    op.drop_column("strategy_task_integration", "automation_attempts")
