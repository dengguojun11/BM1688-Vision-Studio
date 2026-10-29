"""Add optional creator-scoped retry keys to task and YOLO train creation."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "za0b1c2d3e4f"
down_revision = "z9f3a4b5c6d7"
branch_labels = None
depends_on = None


_TABLES = {
    "task": "uq_task_creator_idempotency",
    "yolo_train_job": "uq_yolo_train_job_creator_idempotency",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, constraint_name in _TABLES.items():
        if table not in inspector.get_table_names():
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        existing_constraints = {
            item.get("name") for item in inspector.get_unique_constraints(table)
        }
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                if "idempotency_key" not in columns:
                    batch.add_column(sa.Column("idempotency_key", sa.String(256), nullable=True))
                if "idempotency_request_hash" not in columns:
                    batch.add_column(
                        sa.Column("idempotency_request_hash", sa.String(64), nullable=True)
                    )
                if constraint_name not in existing_constraints:
                    batch.create_unique_constraint(
                        constraint_name, ["created_by", "idempotency_key"]
                    )
            continue
        if "idempotency_key" not in columns:
            op.add_column(table, sa.Column("idempotency_key", sa.String(256), nullable=True))
        if "idempotency_request_hash" not in columns:
            op.add_column(
                table,
                sa.Column("idempotency_request_hash", sa.String(64), nullable=True),
            )
        if constraint_name not in existing_constraints:
            op.create_unique_constraint(
                constraint_name, table, ["created_by", "idempotency_key"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, constraint_name in reversed(tuple(_TABLES.items())):
        if table not in inspector.get_table_names():
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        existing_constraints = {
            item.get("name") for item in inspector.get_unique_constraints(table)
        }
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                if constraint_name in existing_constraints:
                    batch.drop_constraint(constraint_name, type_="unique")
                if "idempotency_request_hash" in columns:
                    batch.drop_column("idempotency_request_hash")
                if "idempotency_key" in columns:
                    batch.drop_column("idempotency_key")
            continue
        if constraint_name in existing_constraints:
            op.drop_constraint(constraint_name, table, type_="unique")
        if "idempotency_request_hash" in columns:
            op.drop_column(table, "idempotency_request_hash")
        if "idempotency_key" in columns:
            op.drop_column(table, "idempotency_key")
