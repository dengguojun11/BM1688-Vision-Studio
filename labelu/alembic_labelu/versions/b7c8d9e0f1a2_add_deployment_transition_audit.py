"""add append-only deployment transition audit and fencing generations"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if "deployment_release" in tables and "fencing_token" not in _columns("deployment_release"):
        op.add_column(
            "deployment_release",
            sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        )
    if "deployment_target" in tables and "fencing_token" not in _columns("deployment_target"):
        op.add_column(
            "deployment_target",
            sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        )

    if "deployment_transition_audit" not in tables:
        op.create_table(
            "deployment_transition_audit",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("release_id", sa.Integer(), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=True),
            sa.Column("operation", sa.String(64), nullable=False),
            sa.Column("actor_type", sa.String(24), nullable=False),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("agent_id", sa.String(128), nullable=True),
            sa.Column("from_status", sa.String(32), nullable=True),
            sa.Column("to_status", sa.String(32), nullable=True),
            sa.Column("result", sa.String(24), nullable=False, server_default="APPLIED"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("request_id", sa.String(128), nullable=True),
            sa.Column("correlation_id", sa.String(128), nullable=True),
            sa.Column("idempotency_key", sa.String(160), nullable=True),
            sa.Column("fencing_token", sa.Integer(), nullable=True),
            sa.Column("before_snapshot_json", sa.Text(), nullable=True),
            sa.Column("after_snapshot_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["release_id"], ["deployment_release.id"]
            ),
            sa.ForeignKeyConstraint(
                ["target_id"], ["deployment_target.id"]
            ),
            sa.ForeignKeyConstraint(["actor_id"], ["user.id"]),
            sa.UniqueConstraint(
                "idempotency_key", name="uq_deployment_transition_audit_idempotency"
            ),
        )

    indexes = _indexes("deployment_transition_audit")
    wanted = {
        "ix_deployment_transition_audit_release_id": ["release_id"],
        "ix_deployment_transition_audit_target_id": ["target_id"],
        "ix_deployment_transition_audit_actor_id": ["actor_id"],
        "ix_deployment_transition_audit_agent_id": ["agent_id"],
        "ix_deployment_transition_audit_request_id": ["request_id"],
        "ix_deployment_transition_audit_correlation_id": ["correlation_id"],
        "ix_deployment_transition_audit_created_at": ["created_at"],
        "idx_deployment_transition_audit_release_time": ["release_id", "created_at"],
        "idx_deployment_transition_audit_target_time": ["target_id", "created_at"],
        "idx_deployment_transition_audit_actor_time": ["actor_type", "actor_id", "created_at"],
        "idx_deployment_transition_audit_operation_time": ["operation", "created_at"],
    }
    for name, columns in wanted.items():
        if name not in indexes:
            op.create_index(name, "deployment_transition_audit", columns)


def downgrade() -> None:
    if "deployment_transition_audit" in _tables():
        op.drop_table("deployment_transition_audit")
    if "deployment_target" in _tables() and "fencing_token" in _columns("deployment_target"):
        op.drop_column("deployment_target", "fencing_token")
    if "deployment_release" in _tables() and "fencing_token" in _columns("deployment_release"):
        op.drop_column("deployment_release", "fencing_token")
