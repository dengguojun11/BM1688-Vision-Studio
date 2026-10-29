"""add 7GAI edge Agent identity and OTA control plane"""

from alembic import op
import sqlalchemy as sa


revision = "z9a0b1c2d3e4"
down_revision = "y8z9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edge_agent",
        sa.Column("agent_id", sa.String(length=128), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="REGISTERED"),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=True),
        sa.Column("desired_state_json", sa.Text(), nullable=True),
        sa.Column("reported_state_json", sa.Text(), nullable=True),
        sa.Column("last_ip", sa.String(length=64), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_edge_agent_project_id", "edge_agent", ["project_id"])
    op.create_index("ix_edge_agent_status", "edge_agent", ["status"])
    op.create_index("idx_edge_agent_project_status", "edge_agent", ["project_id", "status", "updated_at"])
    op.create_table(
        "edge_agent_release",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("release_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.String(length=1024), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size", sa.Integer(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="1048576"),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PUBLISHED"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("release_key", name="uq_edge_agent_release_key"),
    )
    op.create_index("ix_edge_agent_release_status", "edge_agent_release", ["status"])
    op.create_index("idx_edge_agent_release_status", "edge_agent_release", ["status", "created_at"])
    op.create_table(
        "edge_agent_deployment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("release_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("downloaded_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_sha256", sa.String(length=64), nullable=True),
        sa.Column("active_version", sa.String(length=64), nullable=True),
        sa.Column("previous_version", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_chunk_index", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["edge_agent_release.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["edge_agent.agent_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("release_id", "agent_id", name="uq_edge_agent_deployment_target"),
    )
    op.create_index("ix_edge_agent_deployment_release_id", "edge_agent_deployment", ["release_id"])
    op.create_index("ix_edge_agent_deployment_agent_id", "edge_agent_deployment", ["agent_id"])
    op.create_index("ix_edge_agent_deployment_state", "edge_agent_deployment", ["state"])
    op.create_index("idx_edge_agent_deployment_agent_state", "edge_agent_deployment", ["agent_id", "state", "updated_at"])


def downgrade() -> None:
    op.drop_table("edge_agent_deployment")
    op.drop_table("edge_agent_release")
    op.drop_table("edge_agent")
