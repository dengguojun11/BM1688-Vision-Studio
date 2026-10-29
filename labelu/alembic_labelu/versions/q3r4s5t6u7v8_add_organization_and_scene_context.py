"""add organization and project-scoped scene context"""

from alembic import op
import sqlalchemy as sa


revision = "q3r4s5t6u7v8"
down_revision = "p2q3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("owner_id", "name", name="uq_organization_owner_name"),
    )
    op.create_index("idx_organization_owner", "organization", ["owner_id", "status", "deleted_at"])
    op.add_column(
        "project",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organization.id"), nullable=True),
    )
    op.create_index("idx_project_organization", "project", ["organization_id", "status", "deleted_at"])
    op.create_table(
        "scene",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "scene_key", name="uq_scene_project_key"),
    )
    op.create_index("idx_scene_project", "scene", ["project_id", "status", "deleted_at"])


def downgrade() -> None:
    op.drop_index("idx_scene_project", table_name="scene")
    op.drop_table("scene")
    op.drop_index("idx_project_organization", table_name="project")
    op.drop_column("project", "organization_id")
    op.drop_index("idx_organization_owner", table_name="organization")
    op.drop_table("organization")
