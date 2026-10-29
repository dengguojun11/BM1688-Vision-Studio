"""add project boundaries and project membership"""

from alembic import op
import sqlalchemy as sa


revision = "i8j9k0l1m2n3"
down_revision = "h7i8j9k0l1m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_owner_id", "project", ["owner_id"], unique=False)
    op.create_index("ix_project_status", "project", ["status"], unique=False)
    op.create_index("ix_project_deleted_at", "project", ["deleted_at"], unique=False)
    op.create_index(
        "idx_project_owner_status", "project", ["owner_id", "status", "deleted_at"], unique=False
    )
    op.create_index(
        "idx_project_status_updated", "project", ["status", "updated_at"], unique=False
    )

    op.create_table(
        "project_member",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invited_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "user_id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member_project_user"),
    )
    op.create_index("ix_project_member_status", "project_member", ["status"], unique=False)
    op.create_index(
        "idx_project_member_user_status", "project_member", ["user_id", "status"], unique=False
    )
    op.create_index(
        "idx_project_member_project_status", "project_member", ["project_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("idx_project_member_project_status", table_name="project_member")
    op.drop_index("idx_project_member_user_status", table_name="project_member")
    op.drop_index("ix_project_member_status", table_name="project_member")
    op.drop_table("project_member")
    op.drop_index("idx_project_status_updated", table_name="project")
    op.drop_index("idx_project_owner_status", table_name="project")
    op.drop_index("ix_project_deleted_at", table_name="project")
    op.drop_index("ix_project_status", table_name="project")
    op.drop_index("ix_project_owner_id", table_name="project")
    op.drop_table("project")
