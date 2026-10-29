"""add published model catalog and identifier aliases"""

from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "published_model_catalog",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_model_id", sa.String(length=128), nullable=False),
        sa.Column("aicm_environment_id", sa.String(length=64), nullable=True),
        sa.Column("aicm_model_set_id", sa.Integer(), nullable=True),
        sa.Column("export_id", sa.Integer(), nullable=True),
        sa.Column("yolo_train_job_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column(
            "display_name", sa.String(length=256), nullable=False, server_default=""
        ),
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default="labelu_publish",
        ),
        sa.Column(
            "source_system", sa.String(length=64), nullable=False, server_default="aicm"
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="active"
        ),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "availability_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("chip_type", sa.String(length=128), nullable=True),
        sa.Column("algorithm_type", sa.String(length=64), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.Column("map50", sa.String(length=32), nullable=True),
        sa.Column("labels_json", sa.Text(), nullable=True),
        sa.Column("remote_download_url", sa.Text(), nullable=True),
        sa.Column("package_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("package_sha256", sa.String(length=64), nullable=True),
        sa.Column("package_md5", sa.String(length=32), nullable=True),
        sa.Column("publisher_username", sa.String(length=128), nullable=True),
        sa.Column("publish_job_id", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["export_id"], ["yolo_bm1688_export.id"]),
        sa.ForeignKeyConstraint(["yolo_train_job_id"], ["yolo_train_job.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.UniqueConstraint(
            "public_model_id", name="uq_published_model_catalog_public_model_id"
        ),
        sa.UniqueConstraint(
            "aicm_environment_id",
            "aicm_model_set_id",
            name="uq_published_model_catalog_aicm_model",
        ),
    )
    for column in (
        "id",
        "public_model_id",
        "aicm_environment_id",
        "aicm_model_set_id",
        "export_id",
        "yolo_train_job_id",
        "task_id",
        "source_type",
        "source_system",
        "status",
        "is_public",
        "availability_status",
        "publish_job_id",
        "published_at",
        "deleted_at",
    ):
        op.create_index(
            f"ix_published_model_catalog_{column}", "published_model_catalog", [column]
        )

    op.create_table(
        "published_model_identifier_alias",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("catalog_id", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("external_model_id", sa.String(length=128), nullable=False),
        sa.Column(
            "alias_type",
            sa.String(length=32),
            nullable=False,
            server_default="third_party",
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_id"], ["published_model_catalog.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.UniqueConstraint(
            "source_system",
            "external_model_id",
            name="uq_published_model_identifier_alias_source_id",
        ),
    )
    for column in (
        "id",
        "catalog_id",
        "source_system",
        "external_model_id",
        "created_by",
    ):
        op.create_index(
            f"ix_published_model_identifier_alias_{column}",
            "published_model_identifier_alias",
            [column],
        )


def downgrade() -> None:
    op.drop_table("published_model_identifier_alias")
    op.drop_table("published_model_catalog")
