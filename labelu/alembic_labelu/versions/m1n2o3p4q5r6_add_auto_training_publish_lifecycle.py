"""add persisted AICM publication lifecycle to automatic training rounds

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
"""

from alembic import op
import sqlalchemy as sa


revision = "m1n2o3p4q5r6"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    columns = _columns("auto_training_round")
    additions = [
        sa.Column("publish_job_id", sa.String(64), nullable=True),
        sa.Column("publish_status", sa.String(32), nullable=True),
        sa.Column("published_model_id", sa.Integer(), nullable=True),
        sa.Column("publish_requested_by", sa.Integer(), nullable=True),
        sa.Column("publish_reason", sa.Text(), nullable=True),
        sa.Column("publish_error", sa.Text(), nullable=True),
        sa.Column("publish_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("auto_training_round", column)

    indexes = _indexes("auto_training_round")
    if "ix_auto_training_round_publish_job_id" not in indexes:
        op.create_index(
            "ix_auto_training_round_publish_job_id",
            "auto_training_round",
            ["publish_job_id"],
        )
    if "ix_auto_training_round_publish_status" not in indexes:
        op.create_index(
            "ix_auto_training_round_publish_status",
            "auto_training_round",
            ["publish_status"],
        )
    if "ix_auto_training_round_published_model_id" not in indexes:
        op.create_index(
            "ix_auto_training_round_published_model_id",
            "auto_training_round",
            ["published_model_id"],
        )


def downgrade():
    indexes = _indexes("auto_training_round")
    for name in (
        "ix_auto_training_round_published_model_id",
        "ix_auto_training_round_publish_status",
        "ix_auto_training_round_publish_job_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="auto_training_round")
    columns = _columns("auto_training_round")
    for name in (
        "published_at",
        "publish_requested_at",
        "publish_error",
        "publish_reason",
        "publish_requested_by",
        "published_model_id",
        "publish_status",
        "publish_job_id",
    ):
        if name in columns:
            op.drop_column("auto_training_round", name)
