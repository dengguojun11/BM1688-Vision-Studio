"""add project-scoped rule center"""
from alembic import op
import sqlalchemy as sa

revision = "m9n0p1q2r3s4"
down_revision = "l8m9n0p1q2r3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_key", sa.String(128), nullable=False), sa.Column("name", sa.String(160), nullable=False),
        sa.Column("algorithm_key", sa.String(128), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "template_key", name="uq_rule_template_project_key"),
    )
    op.create_index("idx_rule_template_project_status", "rule_template", ["project_id", "status"])
    op.create_table(
        "rule_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("rule_template.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("schema_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("config_json", sa.Text(), nullable=False), sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_id", "version_no", name="uq_rule_version_template_version"),
    )
    op.create_index("idx_rule_version_template_status", "rule_version", ["template_id", "status"])
    op.create_table(
        "rule_binding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False), sa.Column("rule_version_id", sa.Integer(), sa.ForeignKey("rule_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="INACTIVE"), sa.Column("active_key", sa.String(128), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "active_key", name="uq_rule_binding_device_active"),
    )
    op.create_index("idx_rule_binding_project_status", "rule_binding", ["project_id", "status"])
    op.create_index("idx_rule_binding_device_status", "rule_binding", ["device_id", "status"])
    op.create_table(
        "rule_binding_audit",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("binding_id", sa.Integer(), sa.ForeignKey("rule_binding.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False), sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False), sa.Column("from_status", sa.String(24)), sa.Column("to_status", sa.String(24)),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("user.id")), sa.Column("reason", sa.Text()), sa.Column("idempotency_key", sa.String(160)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_rule_binding_audit_idempotency"),
    )
    op.create_index("idx_rule_binding_audit_project_time", "rule_binding_audit", ["project_id", "created_at"])
    op.create_index("idx_rule_binding_audit_binding_time", "rule_binding_audit", ["binding_id", "created_at"])


def downgrade() -> None:
    op.drop_table("rule_binding_audit")
    op.drop_table("rule_binding")
    op.drop_table("rule_version")
    op.drop_table("rule_template")
