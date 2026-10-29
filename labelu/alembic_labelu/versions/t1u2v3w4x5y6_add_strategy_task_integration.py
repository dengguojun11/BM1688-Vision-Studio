"""add strategy task integration

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-07-01 16:20:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "v3w4x5y6z7a8"
down_revision = "u2v3w4x5y6z7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategy_task_integration",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_task_id", sa.String(length=128), nullable=False),
        sa.Column("labelu_task_id", sa.Integer(), nullable=True),
        sa.Column("datasource_id", sa.Integer(), nullable=True),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("dataset_s3_uri", sa.String(length=1024), nullable=False),
        sa.Column("dataset_image_prefix", sa.String(length=512), nullable=False),
        sa.Column("metadata_key", sa.String(length=512), nullable=False),
        sa.Column("bucket", sa.String(length=256), nullable=False),
        sa.Column("prefix", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("labeling_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("labeling_media_type", sa.String(length=32), nullable=True),
        sa.Column("labeling_annotation_type", sa.String(length=64), nullable=True),
        sa.Column("labeling_labels_json", sa.Text(), nullable=True),
        sa.Column("target_json", sa.Text(), nullable=True),
        sa.Column("experiment_json", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("import_status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("imported_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_pre_annotation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["datasource_id"], ["data_source.id"]),
        sa.ForeignKeyConstraint(["labelu_task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_task_id",
            name="uq_strategy_task_integration_external_task_id",
        ),
    )
    op.create_index(
        op.f("ix_strategy_task_integration_id"),
        "strategy_task_integration",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_external_task_id"),
        "strategy_task_integration",
        ["external_task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_labelu_task_id"),
        "strategy_task_integration",
        ["labelu_task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_datasource_id"),
        "strategy_task_integration",
        ["datasource_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_created_by"),
        "strategy_task_integration",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_updated_by"),
        "strategy_task_integration",
        ["updated_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_strategy_task_integration_deleted_at"),
        "strategy_task_integration",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "idx_strategy_task_integration_task_deleted",
        "strategy_task_integration",
        ["labelu_task_id", "deleted_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_strategy_task_integration_task_deleted", table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_deleted_at"), table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_updated_by"), table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_created_by"), table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_datasource_id"), table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_labelu_task_id"), table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_external_task_id"), table_name="strategy_task_integration")
    op.drop_index(op.f("ix_strategy_task_integration_id"), table_name="strategy_task_integration")
    op.drop_table("strategy_task_integration")
