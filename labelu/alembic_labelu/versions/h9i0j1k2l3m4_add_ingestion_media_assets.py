"""Add the project-scoped 7GAI ingestion and media-asset boundary."""

from alembic import op
import sqlalchemy as sa


revision = "h9i0j1k2l3m4"
down_revision = "g8h9i0j1k2l3"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "ingestion_job" not in tables:
        op.create_table(
            "ingestion_job",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("scene_id", sa.Integer(), nullable=True),
            sa.Column("source_connection_id", sa.Integer(), nullable=True),
            sa.Column("source_type", sa.String(length=32), nullable=False, server_default="UPLOAD"),
            sa.Column("idempotency_key", sa.String(length=256), nullable=False),
            sa.Column("source_snapshot_json", sa.Text(), nullable=True),
            sa.Column("selection_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
            sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("quarantined_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_connection_id"], ["data_source.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id", "idempotency_key", name="uq_ingestion_job_project_idempotency"
            ),
        )
    tables = _tables(bind)
    if "media_asset" not in tables:
        op.create_table(
            "media_asset",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("scene_id", sa.Integer(), nullable=True),
            sa.Column("ingestion_job_id", sa.Integer(), nullable=True),
            sa.Column("object_uri", sa.String(length=1024), nullable=False),
            sa.Column("source_object_key", sa.String(length=1024), nullable=True),
            sa.Column("media_type", sa.String(length=32), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.BigInteger(), nullable=True),
            sa.Column("frame_index", sa.Integer(), nullable=True),
            sa.Column("scan_status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("scan_provider", sa.String(length=64), nullable=True),
            sa.Column("scan_signature", sa.String(length=256), nullable=True),
            sa.Column("scan_error", sa.Text(), nullable=True),
            sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("lifecycle_status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("attachment_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ingestion_job_id"], ["ingestion_job.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id", "sha256", "media_type", name="uq_media_asset_project_sha_media"
            ),
        )

    for table, name, columns in (
        ("ingestion_job", "idx_ingestion_job_project_status", ["project_id", "status", "updated_at"]),
        ("ingestion_job", "idx_ingestion_job_source", ["source_type", "source_connection_id"]),
        ("media_asset", "idx_media_asset_project_scan", ["project_id", "scan_status", "lifecycle_status"]),
        ("media_asset", "idx_media_asset_ingestion", ["ingestion_job_id", "created_at"]),
        ("media_asset", "idx_media_asset_scene", ["project_id", "scene_id", "created_at"]),
    ):
        if name not in _indexes(bind, table):
            op.create_index(name, table, columns, unique=False)

    # Compatibility columns are nullable and therefore safe for historical
    # rows.  Existing media upload/storage code does not need to change.
    if "data_source" in tables:
        columns = _columns(bind, "data_source")
        if "project_id" not in columns:
            op.add_column("data_source", sa.Column("project_id", sa.Integer(), nullable=True))
        if "status" not in columns:
            op.add_column("data_source", sa.Column("status", sa.String(length=24), nullable=True, server_default="ACTIVE"))
        if "idx_data_source_project_status" not in _indexes(bind, "data_source"):
            op.create_index("idx_data_source_project_status", "data_source", ["project_id", "status"], unique=False)
    if "task_attachment" in tables:
        columns = _columns(bind, "task_attachment")
        if "media_asset_id" not in columns:
            op.add_column("task_attachment", sa.Column("media_asset_id", sa.Integer(), nullable=True))
        if "idx_task_attachment_media_asset" not in _indexes(bind, "task_attachment"):
            op.create_index("idx_task_attachment_media_asset", "task_attachment", ["media_asset_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "task_attachment" in tables and "idx_task_attachment_media_asset" in _indexes(bind, "task_attachment"):
        op.drop_index("idx_task_attachment_media_asset", table_name="task_attachment")
    if "task_attachment" in tables and "media_asset_id" in _columns(bind, "task_attachment"):
        op.drop_column("task_attachment", "media_asset_id")
    if "data_source" in tables and "idx_data_source_project_status" in _indexes(bind, "data_source"):
        op.drop_index("idx_data_source_project_status", table_name="data_source")
    if "data_source" in tables:
        columns = _columns(bind, "data_source")
        if "status" in columns:
            op.drop_column("data_source", "status")
        if "project_id" in columns:
            op.drop_column("data_source", "project_id")
    if "media_asset" in tables:
        for name in ("idx_media_asset_scene", "idx_media_asset_ingestion", "idx_media_asset_project_scan"):
            if name in _indexes(bind, "media_asset"):
                op.drop_index(name, table_name="media_asset")
        op.drop_table("media_asset")
    if "ingestion_job" in tables:
        for name in ("idx_ingestion_job_source", "idx_ingestion_job_project_status"):
            if name in _indexes(bind, "ingestion_job"):
                op.drop_index(name, table_name="ingestion_job")
        op.drop_table("ingestion_job")
