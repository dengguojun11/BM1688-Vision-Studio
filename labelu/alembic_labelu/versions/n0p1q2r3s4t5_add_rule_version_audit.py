"""add append-only rule version publication audit"""

from alembic import op
import sqlalchemy as sa


revision = "n0p1q2r3s4t5"
down_revision = "m9n0p1q2r3s4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_version_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("rule_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("rule_template.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_rule_version_audit_idempotency"),
    )
    op.create_index("idx_rule_version_audit_project_time", "rule_version_audit", ["project_id", "created_at"])
    op.create_index("idx_rule_version_audit_version_time", "rule_version_audit", ["version_id", "created_at"])


def downgrade() -> None:
    op.drop_table("rule_version_audit")
