"""link auto training to existing pipeline

Revision ID: k9l0m1n2o3p4
Revises: j8k9l0m1n2o3
"""

from alembic import op
import sqlalchemy as sa


revision = "k9l0m1n2o3p4"
down_revision = "j8k9l0m1n2o3"
branch_labels = None
depends_on = None


def _columns(table):
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table, column):
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade():
    links = {
        "training_task": [
            sa.Column("auto_training_plan_id", sa.Integer()), sa.Column("auto_training_round_id", sa.Integer()),
            sa.Column("source_mode", sa.String(16)),
        ],
        "yolo_train_job": [
            sa.Column("auto_training_plan_id", sa.Integer()), sa.Column("auto_training_round_id", sa.Integer()),
            sa.Column("queue_priority", sa.Integer()), sa.Column("queue_source", sa.String(16)),
            sa.Column("queue_lease_owner", sa.String(128)), sa.Column("queue_lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("execution_signature", sa.String(64)),
        ],
        "yolo_bm1688_export": [
            sa.Column("auto_training_plan_id", sa.Integer()), sa.Column("auto_training_round_id", sa.Integer()),
            sa.Column("calibration_manifest_id", sa.Integer()), sa.Column("execution_signature", sa.String(64)),
            sa.Column("pipeline_origin", sa.String(32)),
        ],
        "yolo_edge_validation_job": [
            sa.Column("auto_training_plan_id", sa.Integer()), sa.Column("auto_training_round_id", sa.Integer()),
            sa.Column("deployment_id", sa.Integer()), sa.Column("execution_signature", sa.String(64)),
            sa.Column("pipeline_origin", sa.String(32)),
        ],
    }
    for table, columns in links.items():
        for column in columns:
            _add(table, column)


def downgrade():
    pass
