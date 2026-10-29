"""Add optional project ownership to edge OTA releases."""

from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "edge_agent_release" not in tables:
        return
    columns = {item["name"] for item in inspector.get_columns("edge_agent_release")}
    if "project_id" not in columns:
        op.add_column("edge_agent_release", sa.Column("project_id", sa.Integer(), nullable=True))
        if bind.dialect.name != "sqlite" and "project" in tables:
            op.create_foreign_key(
                "fk_edge_agent_release_project_id_project",
                "edge_agent_release",
                "project",
                ["project_id"],
                ["id"],
            )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("edge_agent_release")}
    if "ix_edge_agent_release_project_id" not in indexes:
        op.create_index("ix_edge_agent_release_project_id", "edge_agent_release", ["project_id"])
    if "idx_edge_agent_release_project_status" not in indexes:
        op.create_index(
            "idx_edge_agent_release_project_status",
            "edge_agent_release",
            ["project_id", "status", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "edge_agent_release" not in sa.inspect(bind).get_table_names():
        return
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("edge_agent_release")}
    for name in ("idx_edge_agent_release_project_status", "ix_edge_agent_release_project_id"):
        if name in indexes:
            op.drop_index(name, table_name="edge_agent_release")
    columns = {item["name"] for item in sa.inspect(bind).get_columns("edge_agent_release")}
    if "project_id" in columns:
        if bind.dialect.name != "sqlite":
            try:
                op.drop_constraint("fk_edge_agent_release_project_id_project", "edge_agent_release", type_="foreignkey")
            except Exception:
                pass
        op.drop_column("edge_agent_release", "project_id")
