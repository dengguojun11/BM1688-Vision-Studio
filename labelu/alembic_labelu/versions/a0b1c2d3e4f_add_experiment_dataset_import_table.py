"""add experiment dataset import table

Revision ID: a0b1c2d3e4f
Revises: z8a9b0c1d2e
Create Date: 2026-07-06 17:45:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a0b1c2d3e4f"
down_revision = "z8a9b0c1d2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "experiment_dataset_import" in inspector.get_table_names():
        return

    op.create_table(
        "experiment_dataset_import",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_type", sa.String(length=64), nullable=False),
        sa.Column("dataset_date", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("target_signature", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source_payload_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("formal_task_ids_json", sa.Text(), nullable=False),
        sa.Column("draft_task_id", sa.Integer(), nullable=True),
        sa.Column("declared_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("formal_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draft_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("abnormal_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["draft_task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", name="uq_experiment_dataset_import_package_id"),
    )
    op.create_index("ix_experiment_dataset_import_id", "experiment_dataset_import", ["id"])
    op.create_index(
        "ix_experiment_dataset_import_package_id",
        "experiment_dataset_import",
        ["package_id"],
    )
    op.create_index(
        "ix_experiment_dataset_import_deleted_at",
        "experiment_dataset_import",
        ["deleted_at"],
    )
    op.create_index(
        "idx_experiment_dataset_import_status_deleted",
        "experiment_dataset_import",
        ["status", "deleted_at"],
    )
    op.create_index(
        "ix_experiment_dataset_import_draft_task_id",
        "experiment_dataset_import",
        ["draft_task_id"],
    )
    op.create_index(
        "ix_experiment_dataset_import_created_by",
        "experiment_dataset_import",
        ["created_by"],
    )
    op.create_index(
        "ix_experiment_dataset_import_updated_by",
        "experiment_dataset_import",
        ["updated_by"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "experiment_dataset_import" not in inspector.get_table_names():
        return

    op.drop_index("ix_experiment_dataset_import_updated_by", table_name="experiment_dataset_import")
    op.drop_index("ix_experiment_dataset_import_created_by", table_name="experiment_dataset_import")
    op.drop_index("ix_experiment_dataset_import_draft_task_id", table_name="experiment_dataset_import")
    op.drop_index("idx_experiment_dataset_import_status_deleted", table_name="experiment_dataset_import")
    op.drop_index("ix_experiment_dataset_import_deleted_at", table_name="experiment_dataset_import")
    op.drop_index("ix_experiment_dataset_import_package_id", table_name="experiment_dataset_import")
    op.drop_index("ix_experiment_dataset_import_id", table_name="experiment_dataset_import")
    op.drop_table("experiment_dataset_import")
