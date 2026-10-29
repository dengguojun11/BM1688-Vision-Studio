"""add project and dataset-version lineage to automatic training plans

Revision ID: x7y8z9a0b1c2
Revises: w6x7y8z9a0b
"""

from alembic import op
import sqlalchemy as sa


revision = "x7y8z9a0b1c2"
down_revision = "w6x7y8z9a0b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "auto_training_plan" not in tables:
        return
    columns = {item["name"] for item in inspector.get_columns("auto_training_plan")}
    if "project_id" not in columns:
        op.add_column(
            "auto_training_plan",
            sa.Column("project_id", sa.Integer(), nullable=True),
        )
    if "source_dataset_version_id" not in columns:
        op.add_column(
            "auto_training_plan",
            sa.Column("source_dataset_version_id", sa.Integer(), nullable=True),
        )
    indexes = {item["name"] for item in inspector.get_indexes("auto_training_plan")}
    if "ix_auto_training_plan_project_id" not in indexes:
        op.create_index(
            "ix_auto_training_plan_project_id",
            "auto_training_plan",
            ["project_id"],
            unique=False,
        )
    if "ix_auto_training_plan_source_dataset_version_id" not in indexes:
        op.create_index(
            "ix_auto_training_plan_source_dataset_version_id",
            "auto_training_plan",
            ["source_dataset_version_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "auto_training_plan" not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes("auto_training_plan")}
    if "ix_auto_training_plan_source_dataset_version_id" in indexes:
        op.drop_index(
            "ix_auto_training_plan_source_dataset_version_id",
            table_name="auto_training_plan",
        )
    if "ix_auto_training_plan_project_id" in indexes:
        op.drop_index("ix_auto_training_plan_project_id", table_name="auto_training_plan")
    columns = {item["name"] for item in inspector.get_columns("auto_training_plan")}
    if "source_dataset_version_id" in columns:
        op.drop_column("auto_training_plan", "source_dataset_version_id")
    if "project_id" in columns:
        op.drop_column("auto_training_plan", "project_id")
