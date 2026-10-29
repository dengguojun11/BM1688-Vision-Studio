"""add edge inference events and deduplicated hard examples"""

from alembic import op
import sqlalchemy as sa

revision = "q9r0s1t2u3v4"
down_revision = "aa7gaiwf0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "inference_event" not in tables:
        op.create_table(
            "inference_event",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
            sa.Column("device_id", sa.String(128), nullable=False),
            sa.Column("model_version_id", sa.Integer(), sa.ForeignKey("model_version.id"), nullable=True),
            sa.Column("event_key", sa.String(160), nullable=False),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Integer(), nullable=True),
            sa.Column("asset_uri", sa.String(1024), nullable=True),
            sa.Column("asset_sha256", sa.String(64), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "event_key", name="uq_inference_event_key"),
        )
        op.create_index("idx_inference_event_project_created", "inference_event", ["project_id", "created_at"])
        op.create_index("ix_inference_event_project_id", "inference_event", ["project_id"])
        op.create_index("ix_inference_event_device_id", "inference_event", ["device_id"])
        op.create_index("ix_inference_event_model_version_id", "inference_event", ["model_version_id"])
        op.create_index("ix_inference_event_created_at", "inference_event", ["created_at"])
    if "edge_hard_example" not in tables:
        op.create_table(
            "edge_hard_example",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
            sa.Column("inference_event_id", sa.Integer(), sa.ForeignKey("inference_event.id"), nullable=False),
            sa.Column("device_id", sa.String(128), nullable=False),
            sa.Column("model_version_id", sa.Integer(), sa.ForeignKey("model_version.id"), nullable=True),
            sa.Column("fingerprint", sa.String(128), nullable=False),
            sa.Column("asset_uri", sa.String(1024), nullable=True),
            sa.Column("confidence", sa.Integer(), nullable=True),
            sa.Column("hard_example_type", sa.String(32), nullable=False),
            sa.Column("review_status", sa.String(24), nullable=False),
            sa.Column("review_reason", sa.Text(), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("training_status", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "fingerprint", name="uq_edge_hard_example_fingerprint"),
        )
        op.create_index("idx_edge_hard_example_queue", "edge_hard_example", ["project_id", "review_status", "training_status"])
        for name, columns in (
            ("ix_edge_hard_example_project_id", ["project_id"]),
            ("ix_edge_hard_example_inference_event_id", ["inference_event_id"]),
            ("ix_edge_hard_example_device_id", ["device_id"]),
            ("ix_edge_hard_example_model_version_id", ["model_version_id"]),
            ("ix_edge_hard_example_review_status", ["review_status"]),
            ("ix_edge_hard_example_training_status", ["training_status"]),
        ):
            op.create_index(name, "edge_hard_example", columns)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "edge_hard_example" in tables:
        op.drop_table("edge_hard_example")
    if "inference_event" in tables:
        op.drop_table("inference_event")
