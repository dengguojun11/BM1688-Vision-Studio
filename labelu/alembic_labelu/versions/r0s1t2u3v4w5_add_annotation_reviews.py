"""add project-scoped annotation review state machine"""

from alembic import op
import sqlalchemy as sa


revision = "r0s1t2u3v4w5"
down_revision = "q9r0s1t2u3v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "annotation_review" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "annotation_review",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("annotation_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arbitrator_id", sa.Integer(), nullable=True),
        sa.Column("arbitrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arbitration_decision", sa.String(length=24), nullable=True),
        sa.Column("arbitration_reason", sa.Text(), nullable=True),
        sa.Column("invalidated_by", sa.Integer(), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["sample_id"], ["task_sample.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["arbitrator_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["invalidated_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "sample_id", "annotation_sha256", "source_type", name="uq_annotation_review_snapshot"),
    )
    for name, columns in (
        ("ix_annotation_review_project_id", ["project_id"]),
        ("ix_annotation_review_task_id", ["task_id"]),
        ("ix_annotation_review_sample_id", ["sample_id"]),
        ("ix_annotation_review_annotation_sha256", ["annotation_sha256"]),
        ("ix_annotation_review_review_status", ["review_status"]),
        ("idx_annotation_review_project_status", ["project_id", "review_status", "updated_at"]),
        ("idx_annotation_review_sample_status", ["sample_id", "review_status", "updated_at"]),
    ):
        op.create_index(name, "annotation_review", columns)


def downgrade() -> None:
    bind = op.get_bind()
    if "annotation_review" in sa.inspect(bind).get_table_names():
        op.drop_table("annotation_review")
