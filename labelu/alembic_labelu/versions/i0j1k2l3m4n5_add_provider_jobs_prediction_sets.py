"""add ProviderJob and immutable PredictionSet boundaries"""

from alembic import op
import sqlalchemy as sa

revision = "i0j1k2l3m4n5"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "provider_job" not in tables:
        op.create_table(
            "provider_job",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("provider_key", sa.String(length=128), nullable=False),
            sa.Column("provider_version", sa.String(length=64), nullable=False),
            sa.Column("model_version_id", sa.Integer(), nullable=True),
            sa.Column("input_asset_ids_json", sa.Text(), nullable=False),
            sa.Column("contract_version", sa.String(length=64), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=True),
            sa.Column("request_signature", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "request_signature", name="uq_provider_job_project_request"),
        )
    else:
        columns = {item["name"] for item in sa.inspect(bind).get_columns("provider_job")}
        for name, column in (("attempt_count", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")), ("lease_owner", sa.Column("lease_owner", sa.String(length=128), nullable=True)), ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))):
            if name not in columns:
                op.add_column("provider_job", column)
    if "prediction_set" not in _tables(bind):
        op.create_table(
            "prediction_set",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("provider_job_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("annotation_contract_version", sa.String(length=64), nullable=False),
            sa.Column("prediction_schema_version", sa.String(length=64), nullable=False),
            sa.Column("payload_uri", sa.String(length=1024), nullable=True),
            sa.Column("payload_sha256", sa.String(length=64), nullable=True),
            sa.Column("result_manifest_uri", sa.String(length=1024), nullable=True),
            sa.Column("result_sha256", sa.String(length=64), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="COMPLETED"),
            sa.Column("review_status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_reason", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="AI"),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metrics_json", sa.Text(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["provider_job_id"], ["provider_job.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_job_id", "version", name="uq_prediction_set_job_version"),
        )
    else:
        columns = {item["name"] for item in sa.inspect(bind).get_columns("prediction_set")}
        for name, column in (("payload_json", sa.Column("payload_json", sa.Text(), nullable=True)), ("reviewed_by", sa.Column("reviewed_by", sa.Integer(), nullable=True)), ("reviewed_at", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)), ("review_reason", sa.Column("review_reason", sa.Text(), nullable=True))):
            if name not in columns:
                op.add_column("prediction_set", column)
    for table, name, cols in (
        ("provider_job", "idx_provider_job_project_status", ["project_id", "status", "updated_at"]),
        ("provider_job", "idx_provider_job_provider", ["provider_key", "provider_version"]),
        ("prediction_set", "idx_prediction_set_project_review", ["project_id", "review_status", "created_at"]),
        ("prediction_set", "idx_prediction_set_sample", ["sample_id", "created_at"]),
    ):
        if name not in _indexes(bind, table):
            op.create_index(name, table, cols, unique=False)


def downgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "prediction_set" in tables:
        for name in ("idx_prediction_set_sample", "idx_prediction_set_project_review"):
            if name in _indexes(bind, "prediction_set"):
                op.drop_index(name, table_name="prediction_set")
        op.drop_table("prediction_set")
    if "provider_job" in tables:
        for name in ("idx_provider_job_provider", "idx_provider_job_project_status"):
            if name in _indexes(bind, "provider_job"):
                op.drop_index(name, table_name="provider_job")
        op.drop_table("provider_job")
