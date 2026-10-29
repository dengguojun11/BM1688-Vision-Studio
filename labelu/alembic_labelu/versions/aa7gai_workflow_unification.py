"""add unified 7GAI flexible workflow orchestration tables"""

from alembic import op
import sqlalchemy as sa

revision = "aa7gaiwf0001"
# The repository currently has two active schema lines (the canonical model /
# deployment line and the legacy runtime line).  This revision intentionally
# joins them so a fresh install receives one workflow schema head.
down_revision = ("n3o4p5q6r7s8", "z8a9b0c1d2e4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_instance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workflow_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("current_step", sa.String(96), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_instance_idempotency"),
    )
    op.create_index("ix_workflow_instance_project_id", "workflow_instance", ["project_id"])
    op.create_index("ix_workflow_instance_workflow_type", "workflow_instance", ["workflow_type"])
    op.create_index("ix_workflow_instance_status", "workflow_instance", ["status"])
    op.create_index("idx_workflow_instance_project_status", "workflow_instance", ["project_id", "status"])
    op.create_index("ix_workflow_instance_next_run_at", "workflow_instance", ["next_run_at"])

    op.create_table(
        "workflow_step",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("step_key", sa.String(96), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_json", sa.Text(), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instance.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "step_key", name="uq_workflow_step_key"),
    )
    op.create_index("ix_workflow_step_workflow_id", "workflow_step", ["workflow_id"])
    op.create_index("ix_workflow_step_status", "workflow_step", ["status"])
    op.create_index("ix_workflow_step_available_at", "workflow_step", ["available_at"])
    op.create_index("idx_workflow_step_dispatch", "workflow_step", ["status", "available_at"])

    op.create_table(
        "workflow_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=True),
        sa.Column("step_key", sa.String(96), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instance.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_event_workflow_id", "workflow_event", ["workflow_id"])
    op.create_index("ix_workflow_event_event_type", "workflow_event", ["event_type"])
    op.create_index("ix_workflow_event_created_at", "workflow_event", ["created_at"])

    op.create_table(
        "workflow_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instance.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_outbox_idempotency"),
    )
    op.create_index("ix_workflow_outbox_workflow_id", "workflow_outbox", ["workflow_id"])
    op.create_index("ix_workflow_outbox_event_type", "workflow_outbox", ["event_type"])
    op.create_index("ix_workflow_outbox_status", "workflow_outbox", ["status"])
    op.create_index("ix_workflow_outbox_available_at", "workflow_outbox", ["available_at"])
    op.create_index("ix_workflow_outbox_lease_expires_at", "workflow_outbox", ["lease_expires_at"])
    op.create_index("idx_workflow_outbox_dispatch", "workflow_outbox", ["status", "available_at", "id"])
    op.create_index("idx_workflow_outbox_lease", "workflow_outbox", ["status", "lease_expires_at"])


def downgrade() -> None:
    for index, table in (("idx_workflow_outbox_lease", "workflow_outbox"), ("idx_workflow_outbox_dispatch", "workflow_outbox"), ("ix_workflow_outbox_lease_expires_at", "workflow_outbox"), ("ix_workflow_outbox_available_at", "workflow_outbox"), ("ix_workflow_outbox_status", "workflow_outbox"), ("ix_workflow_outbox_event_type", "workflow_outbox"), ("ix_workflow_outbox_workflow_id", "workflow_outbox")):
        op.drop_index(index, table_name=table)
    op.drop_table("workflow_outbox")
    for index, table in (("ix_workflow_event_created_at", "workflow_event"), ("ix_workflow_event_event_type", "workflow_event"), ("ix_workflow_event_workflow_id", "workflow_event")):
        op.drop_index(index, table_name=table)
    op.drop_table("workflow_event")
    for index, table in (("idx_workflow_step_dispatch", "workflow_step"), ("ix_workflow_step_available_at", "workflow_step"), ("ix_workflow_step_status", "workflow_step"), ("ix_workflow_step_workflow_id", "workflow_step")):
        op.drop_index(index, table_name=table)
    op.drop_table("workflow_step")
    for index, table in (("ix_workflow_instance_next_run_at", "workflow_instance"), ("idx_workflow_instance_project_status", "workflow_instance"), ("ix_workflow_instance_status", "workflow_instance"), ("ix_workflow_instance_workflow_type", "workflow_instance"), ("ix_workflow_instance_project_id", "workflow_instance")):
        op.drop_index(index, table_name=table)
    op.drop_table("workflow_instance")
