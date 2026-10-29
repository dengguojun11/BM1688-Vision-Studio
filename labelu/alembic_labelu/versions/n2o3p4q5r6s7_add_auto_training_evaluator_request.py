"""add trusted evaluator request lineage to automatic training

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
"""

from alembic import op
import sqlalchemy as sa


revision = "n2o3p4q5r6s7"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    columns = _columns("auto_training_evaluation_run")
    additions = [
        sa.Column("request_manifest_rel_path", sa.String(512), nullable=True),
        sa.Column("request_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("evaluator_worker_id", sa.String(128), nullable=True),
        sa.Column("evaluator_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluator_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("auto_training_evaluation_run", column)

    indexes = _indexes("auto_training_evaluation_run")
    if "ix_auto_training_evaluation_run_request_manifest_sha256" not in indexes:
        op.create_index(
            "ix_auto_training_evaluation_run_request_manifest_sha256",
            "auto_training_evaluation_run",
            ["request_manifest_sha256"],
        )
    if "ix_auto_training_evaluation_run_evaluator_lease_expires_at" not in indexes:
        op.create_index(
            "ix_auto_training_evaluation_run_evaluator_lease_expires_at",
            "auto_training_evaluation_run",
            ["evaluator_lease_expires_at"],
        )


def downgrade():
    indexes = _indexes("auto_training_evaluation_run")
    for name in (
        "ix_auto_training_evaluation_run_evaluator_lease_expires_at",
        "ix_auto_training_evaluation_run_request_manifest_sha256",
    ):
        if name in indexes:
            op.drop_index(name, table_name="auto_training_evaluation_run")
    columns = _columns("auto_training_evaluation_run")
    for name in (
        "evaluator_lease_expires_at",
        "evaluator_claimed_at",
        "evaluator_worker_id",
        "request_manifest_sha256",
        "request_manifest_rel_path",
    ):
        if name in columns:
            op.drop_column("auto_training_evaluation_run", name)
