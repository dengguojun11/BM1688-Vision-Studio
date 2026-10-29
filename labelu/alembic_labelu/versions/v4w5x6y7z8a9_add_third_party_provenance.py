"""Add service-account and third-party provenance fields.

The migration is deliberately additive and idempotent so it can upgrade both
SQLite development databases and existing MySQL installations.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None


_COLUMNS = {
    "user": {
        "account_type": sa.String(length=32),
        "is_service_account": sa.Boolean(),
        "interactive_login_enabled": sa.Boolean(),
    },
    "task": {
        "source_type": sa.String(length=32),
        "source_system": sa.String(length=64),
        "source_external_task_id": sa.String(length=128),
        "source_external_package_id": sa.String(length=128),
        "source_integration_id": sa.Integer(),
    },
    "experiment_dataset_import": {
        "source_type": sa.String(length=32),
        "source_system": sa.String(length=64),
        "source_external_task_id": sa.String(length=128),
        "source_external_package_id": sa.String(length=128),
        "source_integration_id": sa.Integer(),
    },
    "yolo_train_job": {
        "source_type": sa.String(length=32),
        "source_system": sa.String(length=64),
        "source_external_task_id": sa.String(length=128),
        "source_external_package_id": sa.String(length=128),
        "source_integration_id": sa.Integer(),
    },
    "yolo_bm1688_export": {
        "source_type": sa.String(length=32),
        "source_system": sa.String(length=64),
        "source_external_task_id": sa.String(length=128),
        "source_external_package_id": sa.String(length=128),
        "source_integration_id": sa.Integer(),
    },
    "yolo_edge_validation_job": {
        "source_type": sa.String(length=32),
        "source_system": sa.String(length=64),
        "source_external_task_id": sa.String(length=128),
        "source_external_package_id": sa.String(length=128),
        "source_integration_id": sa.Integer(),
    },
    "strategy_task_integration": {
        "source_type": sa.String(length=32),
        "source_system": sa.String(length=64),
        "source_external_package_id": sa.String(length=128),
    },
}


def _add_columns() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    for table_name, columns in _COLUMNS.items():
        if table_name not in tables:
            continue
        existing = {item["name"] for item in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name in existing:
                continue
            op.add_column(table_name, sa.Column(column_name, column_type, nullable=True))


def upgrade() -> None:
    _add_columns()
    bind = op.get_bind()
    existing_tables = set(inspect(bind).get_table_names())
    # Existing rows retain their historical meaning; defaults are applied by
    # ORM inserts. Set explicit values for old rows where the columns exist.
    for table_name in _COLUMNS:
        if table_name not in existing_tables:
            continue
        if table_name == "strategy_task_integration":
            op.execute(
                sa.text(
                    "UPDATE strategy_task_integration "
                    "SET source_type='third_party', source_system='strategy_evolution' "
                    "WHERE source_type IS NULL"
                )
            )
        elif table_name in {"task", "experiment_dataset_import", "yolo_train_job", "yolo_bm1688_export", "yolo_edge_validation_job"}:
            op.execute(sa.text(f"UPDATE {table_name} SET source_type='internal' WHERE source_type IS NULL"))
    if "strategy_task_integration" in existing_tables:
        op.execute(sa.text("UPDATE strategy_task_integration SET source_external_package_id=source_package_id WHERE source_external_package_id IS NULL"))
    if "user" in existing_tables:
        op.execute(sa.text("UPDATE user SET account_type='human', is_service_account=0, interactive_login_enabled=1 WHERE account_type IS NULL"))


def downgrade() -> None:
    # Provenance is intentionally retained on downgrade to avoid data loss.
    pass
