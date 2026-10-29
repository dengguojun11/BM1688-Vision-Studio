"""add device-side rule signature verification reports"""

from alembic import op
import sqlalchemy as sa


revision = "p2q3r4s5t6u7"
down_revision = "o1p2q3r4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_binding",
        sa.Column(
            "device_verification_status",
            sa.String(32),
            nullable=False,
            server_default="UNVERIFIED",
        ),
    )
    op.add_column(
        "rule_binding",
        sa.Column(
            "device_report_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("rule_binding", sa.Column("last_device_report_id", sa.String(160), nullable=True))
    op.add_column(
        "rule_binding",
        sa.Column("device_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "rule_device_signature_report",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.String(160), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.String(128), sa.ForeignKey("edge_agent.agent_id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_binding_id", sa.Integer(), sa.ForeignKey("rule_binding.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_version_id", sa.Integer(), sa.ForeignKey("rule_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("key_id", sa.String(128), nullable=False),
        sa.Column("key_state", sa.String(24), nullable=False),
        sa.Column("reported_status", sa.String(24), nullable=False),
        sa.Column("effective_status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("report_id", name="uq_rule_device_signature_report_id"),
        sa.UniqueConstraint(
            "rule_binding_id",
            "sequence",
            name="uq_rule_device_signature_report_binding_sequence",
        ),
    )
    op.create_index(
        "idx_rule_device_signature_report_project_time",
        "rule_device_signature_report",
        ["project_id", "received_at"],
    )
    op.create_index(
        "idx_rule_device_signature_report_device_time",
        "rule_device_signature_report",
        ["device_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_rule_device_signature_report_device_time",
        table_name="rule_device_signature_report",
    )
    op.drop_index(
        "idx_rule_device_signature_report_project_time",
        table_name="rule_device_signature_report",
    )
    op.drop_table("rule_device_signature_report")
    op.drop_column("rule_binding", "device_verified_at")
    op.drop_column("rule_binding", "last_device_report_id")
    op.drop_column("rule_binding", "device_report_sequence")
    op.drop_column("rule_binding", "device_verification_status")
