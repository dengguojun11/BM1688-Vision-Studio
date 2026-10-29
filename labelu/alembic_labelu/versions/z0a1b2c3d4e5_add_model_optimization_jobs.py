"""Add durable model optimization adapter jobs."""

from alembic import op
import sqlalchemy as sa

revision = "z0a1b2c3d4e5"
down_revision = "q3r4s5t6u7v8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_optimization_job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=True),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("adapter_key", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instance.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_version.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_model_optimization_job_idempotency"),
    )
    op.create_index("ix_model_optimization_job_project_id", "model_optimization_job", ["project_id"])
    op.create_index("ix_model_optimization_job_model_version_id", "model_optimization_job", ["model_version_id"])
    op.create_index("ix_model_optimization_job_status", "model_optimization_job", ["status"])
    op.create_index("idx_model_optimization_job_project_status", "model_optimization_job", ["project_id", "status"])
    op.create_index("idx_model_optimization_job_workflow", "model_optimization_job", ["workflow_id"])


def downgrade() -> None:
    for name in (
        "idx_model_optimization_job_workflow",
        "idx_model_optimization_job_project_status",
        "ix_model_optimization_job_status",
        "ix_model_optimization_job_model_version_id",
        "ix_model_optimization_job_project_id",
    ):
        op.drop_index(name, table_name="model_optimization_job")
    op.drop_table("model_optimization_job")
