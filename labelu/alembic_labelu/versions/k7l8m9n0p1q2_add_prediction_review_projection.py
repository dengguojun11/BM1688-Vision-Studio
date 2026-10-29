"""add PredictionSet review audit and legacy projection records"""

from alembic import op
import sqlalchemy as sa


revision = "k7l8m9n0p1q2"
down_revision = "i0j1k2l3m4n5"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "prediction_set_review_audit" not in tables:
        op.create_table(
            "prediction_set_review_audit",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("prediction_set_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("from_status", sa.String(length=24), nullable=False),
            sa.Column("to_status", sa.String(length=24), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("actor_id", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["prediction_set_id"], ["prediction_set.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("prediction_set_id", "idempotency_key", name="uq_prediction_set_review_audit_request"),
        )
    if "prediction_set_projection" not in tables:
        op.create_table(
            "prediction_set_projection",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("prediction_set_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("provider_job_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("pre_annotation_id", sa.Integer(), nullable=False),
            sa.Column("payload_sha256", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PROJECTED"),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["prediction_set_id"], ["prediction_set.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["provider_job_id"], ["provider_job.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["sample_id"], ["task_sample.id"]),
            sa.ForeignKeyConstraint(["pre_annotation_id"], ["task_pre_annotation.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("prediction_set_id", name="uq_prediction_set_projection_set"),
            sa.UniqueConstraint("idempotency_key", name="uq_prediction_set_projection_request"),
        )
    for table, name, cols in (
        ("prediction_set_review_audit", "idx_prediction_set_review_audit_set_time", ["prediction_set_id", "created_at"]),
        ("prediction_set_review_audit", "idx_prediction_set_review_audit_project_time", ["project_id", "created_at"]),
        ("prediction_set_projection", "idx_prediction_set_projection_project_time", ["project_id", "created_at"]),
        ("prediction_set_projection", "idx_prediction_set_projection_sample_time", ["sample_id", "created_at"]),
    ):
        if name not in _indexes(bind, table):
            op.create_index(name, table, cols, unique=False)


def downgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "prediction_set_projection" in tables:
        for name in ("idx_prediction_set_projection_sample_time", "idx_prediction_set_projection_project_time"):
            if name in _indexes(bind, "prediction_set_projection"):
                op.drop_index(name, table_name="prediction_set_projection")
        op.drop_table("prediction_set_projection")
    if "prediction_set_review_audit" in tables:
        for name in ("idx_prediction_set_review_audit_project_time", "idx_prediction_set_review_audit_set_time"):
            if name in _indexes(bind, "prediction_set_review_audit"):
                op.drop_index(name, table_name="prediction_set_review_audit")
        op.drop_table("prediction_set_review_audit")
