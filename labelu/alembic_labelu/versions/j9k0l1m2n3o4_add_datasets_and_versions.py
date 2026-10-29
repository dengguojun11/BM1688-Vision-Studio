"""add canonical data-center datasets and immutable versions"""

from alembic import op
import sqlalchemy as sa

revision = "j9k0l1m2n3o4"
down_revision = "i8j9k0l1m2n3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_project_id", "dataset", ["project_id"], unique=False)
    op.create_index("ix_dataset_owner_id", "dataset", ["owner_id"], unique=False)
    op.create_index("ix_dataset_status", "dataset", ["status"], unique=False)
    op.create_index("ix_dataset_deleted_at", "dataset", ["deleted_at"], unique=False)
    op.create_index(
        "idx_dataset_project_status",
        "dataset",
        ["project_id", "status", "deleted_at"],
        unique=False,
    )
    op.create_index(
        "idx_dataset_owner_updated", "dataset", ["owner_id", "updated_at"], unique=False
    )

    op.create_table(
        "dataset_version",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("manifest_rel_path", sa.String(length=512), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("label_set_signature", sa.String(length=64), nullable=True),
        sa.Column("class_order_signature", sa.String(length=64), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("frozen_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["frozen_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "version_no", name="uq_dataset_version_number"
        ),
        sa.UniqueConstraint("manifest_sha256", name="uq_dataset_version_manifest_sha"),
    )
    op.create_index(
        "ix_dataset_version_project_id", "dataset_version", ["project_id"], unique=False
    )
    op.create_index(
        "ix_dataset_version_status", "dataset_version", ["status"], unique=False
    )
    op.create_index(
        "idx_dataset_version_dataset_status",
        "dataset_version",
        ["dataset_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_dataset_version_manifest_sha",
        "dataset_version",
        ["manifest_sha256"],
        unique=False,
    )
    op.create_index(
        "idx_dataset_version_project_status",
        "dataset_version",
        ["project_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_dataset_version_project_status", table_name="dataset_version")
    op.drop_index("idx_dataset_version_manifest_sha", table_name="dataset_version")
    op.drop_index("idx_dataset_version_dataset_status", table_name="dataset_version")
    op.drop_index("ix_dataset_version_status", table_name="dataset_version")
    op.drop_index("ix_dataset_version_project_id", table_name="dataset_version")
    op.drop_table("dataset_version")
    op.drop_index("idx_dataset_owner_updated", table_name="dataset")
    op.drop_index("idx_dataset_project_status", table_name="dataset")
    op.drop_index("ix_dataset_deleted_at", table_name="dataset")
    op.drop_index("ix_dataset_status", table_name="dataset")
    op.drop_index("ix_dataset_owner_id", table_name="dataset")
    op.drop_index("ix_dataset_project_id", table_name="dataset")
    op.drop_table("dataset")
