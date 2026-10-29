"""Add durable strategy platform callback receipts.

Revision ID: p7q8r9s0t1u2
Revises: n6o7p8q9r0s1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "p7q8r9s0t1u2"
down_revision = "n6o7p8q9r0s1"
branch_labels = None
depends_on = None


TABLE_NAME = "strategy_platform_callback_receipt"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_task_integration_id", sa.Integer(), nullable=False),
        sa.Column("callback_event_id", sa.String(length=128), nullable=False),
        sa.Column("platform_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["strategy_task_integration_id"],
            ["strategy_task_integration.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "strategy_task_integration_id",
            "callback_event_id",
            name="uq_strategy_platform_callback_receipt_task_event",
        ),
    )
    op.create_index(
        "ix_strategy_callback_receipt_task",
        TABLE_NAME,
        ["strategy_task_integration_id"],
        unique=False,
    )

    integration = sa.table(
        "strategy_task_integration",
        sa.column("id", sa.Integer()),
        sa.column("platform_callback_event_id", sa.String(length=128)),
        sa.column("platform_status", sa.String(length=32)),
    )
    receipt = sa.table(
        TABLE_NAME,
        sa.column("strategy_task_integration_id", sa.Integer()),
        sa.column("callback_event_id", sa.String(length=128)),
        sa.column("platform_status", sa.String(length=32)),
    )
    existing_callbacks = bind.execute(
        sa.select(
            integration.c.id,
            integration.c.platform_callback_event_id,
            integration.c.platform_status,
        ).where(
            integration.c.platform_callback_event_id.is_not(None),
            integration.c.platform_callback_event_id != "",
        )
    ).fetchall()
    if existing_callbacks:
        bind.execute(
            receipt.insert(),
            [
                {
                    "strategy_task_integration_id": row.id,
                    "callback_event_id": row.platform_callback_event_id,
                    "platform_status": row.platform_status or "accepted",
                }
                for row in existing_callbacks
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE_NAME in inspect(bind).get_table_names():
        op.drop_table(TABLE_NAME)
