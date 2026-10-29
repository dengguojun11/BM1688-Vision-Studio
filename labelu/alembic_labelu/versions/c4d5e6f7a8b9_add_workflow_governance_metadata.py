"""Add explicit governance metadata to unified 7GAI workflows."""

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def _columns(bind):
    return {item["name"] for item in sa.inspect(bind).get_columns("workflow_instance")}


def _indexes(bind):
    return {item["name"] for item in sa.inspect(bind).get_indexes("workflow_instance")}


def upgrade() -> None:
    bind = op.get_bind()
    if "workflow_instance" not in sa.inspect(bind).get_table_names():
        return
    columns = _columns(bind)
    additions = (
        ("owner_id", sa.Column("owner_id", sa.Integer(), nullable=True)),
        ("approver_id", sa.Column("approver_id", sa.Integer(), nullable=True)),
        ("deadline_at", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True)),
        ("input_snapshot_json", sa.Column("input_snapshot_json", sa.Text(), nullable=True)),
        ("callback_url", sa.Column("callback_url", sa.String(512), nullable=True)),
        ("callback_signature", sa.Column("callback_signature", sa.String(128), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("workflow_instance", column)
    indexes = _indexes(bind)
    if "ix_workflow_instance_owner_id" not in indexes:
        op.create_index("ix_workflow_instance_owner_id", "workflow_instance", ["owner_id"])
    if "ix_workflow_instance_approver_id" not in indexes:
        op.create_index("ix_workflow_instance_approver_id", "workflow_instance", ["approver_id"])
    if "ix_workflow_instance_deadline_at" not in indexes:
        op.create_index("ix_workflow_instance_deadline_at", "workflow_instance", ["deadline_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if "workflow_instance" not in sa.inspect(bind).get_table_names():
        return
    indexes = _indexes(bind)
    for name in ("ix_workflow_instance_deadline_at", "ix_workflow_instance_approver_id", "ix_workflow_instance_owner_id"):
        if name in indexes:
            op.drop_index(name, table_name="workflow_instance")
    columns = _columns(bind)
    for name in ("callback_signature", "callback_url", "input_snapshot_json", "deadline_at", "approver_id", "owner_id"):
        if name in columns:
            op.drop_column("workflow_instance", name)
