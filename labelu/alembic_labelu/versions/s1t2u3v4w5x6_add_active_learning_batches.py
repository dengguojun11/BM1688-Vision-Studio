"""add active-learning feedback clusters and candidate batches"""

from alembic import op
import sqlalchemy as sa

# Keep this revision distinct from the historical plate-recognition migration
# that already uses ``s1t2u3v4w5x6``.
revision = "s2t3u4v5w6x7"
down_revision = "r0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "feedback_cluster" not in tables:
        op.create_table(
            "feedback_cluster",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("cluster_key", sa.String(128), nullable=False),
            sa.Column("event_type", sa.String(32), nullable=True),
            sa.Column("device_id", sa.String(128), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=False),
            sa.Column("average_confidence", sa.Integer(), nullable=True),
            sa.Column("priority_score", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "cluster_key", name="uq_feedback_cluster_project_key"),
        )
        op.create_index("ix_feedback_cluster_project_id", "feedback_cluster", ["project_id"])
        op.create_index("ix_feedback_cluster_status", "feedback_cluster", ["status"])
        op.create_index("idx_feedback_cluster_project_status", "feedback_cluster", ["project_id", "status", "updated_at"])
    if "active_learning_batch" not in tables:
        op.create_table(
            "active_learning_batch",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("batch_key", sa.String(160), nullable=False),
            sa.Column("quota", sa.Integer(), nullable=False),
            sa.Column("selected_count", sa.Integer(), nullable=False),
            sa.Column("accepted_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("scoring_policy_json", sa.Text(), nullable=True),
            sa.Column("workflow_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instance.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "batch_key", name="uq_active_learning_batch_key"),
        )
        op.create_index("ix_active_learning_batch_project_id", "active_learning_batch", ["project_id"])
        op.create_index("ix_active_learning_batch_workflow_id", "active_learning_batch", ["workflow_id"])
        op.create_index("ix_active_learning_batch_status", "active_learning_batch", ["status"])
        op.create_index("idx_active_learning_batch_project_status", "active_learning_batch", ["project_id", "status", "updated_at"])
    if "active_learning_batch_item" not in tables:
        op.create_table(
            "active_learning_batch_item",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("batch_id", sa.Integer(), nullable=False),
            sa.Column("hard_example_id", sa.Integer(), nullable=False),
            sa.Column("score", sa.Integer(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["active_learning_batch.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["hard_example_id"], ["edge_hard_example.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_id", "hard_example_id", name="uq_active_learning_batch_item"),
        )
        op.create_index("ix_active_learning_batch_item_batch_id", "active_learning_batch_item", ["batch_id"])
        op.create_index("ix_active_learning_batch_item_hard_example_id", "active_learning_batch_item", ["hard_example_id"])
        op.create_index("idx_active_learning_batch_item_batch", "active_learning_batch_item", ["batch_id", "rank"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "active_learning_batch_item" in tables:
        op.drop_table("active_learning_batch_item")
    if "active_learning_batch" in tables:
        op.drop_table("active_learning_batch")
    if "feedback_cluster" in tables:
        op.drop_table("feedback_cluster")
