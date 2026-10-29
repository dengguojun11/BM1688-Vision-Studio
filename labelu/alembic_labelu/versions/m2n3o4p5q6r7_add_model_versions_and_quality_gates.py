"""add canonical 7GAI model versions, artifacts and quality evidence"""

from alembic import op
import sqlalchemy as sa

revision = "m2n3o4p5q6r7"
down_revision = "k0l1m2n3o4p5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_version",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("algorithm_id", sa.Integer(), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), nullable=False),
        sa.Column("training_job_id", sa.Integer(), nullable=True),
        sa.Column("parent_model_version_id", sa.Integer(), nullable=True),
        sa.Column("published_catalog_id", sa.Integer(), nullable=True),
        sa.Column("model_key", sa.String(length=160), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("chip_type", sa.String(length=64), nullable=True),
        sa.Column("precision", sa.String(length=16), nullable=True),
        sa.Column("input_contract_json", sa.Text(), nullable=True),
        sa.Column("output_contract_json", sa.Text(), nullable=True),
        sa.Column("preprocess_contract_json", sa.Text(), nullable=True),
        sa.Column("postprocess_contract_json", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("manifest_json", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["algorithm_id"], ["algorithm_definition.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_version.id"]),
        sa.ForeignKeyConstraint(["training_job_id"], ["yolo_train_job.id"]),
        sa.ForeignKeyConstraint(["parent_model_version_id"], ["model_version.id"]),
        sa.ForeignKeyConstraint(
            ["published_catalog_id"], ["published_model_catalog.id"]
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "algorithm_id", "version_no", name="uq_model_version_number"
        ),
        sa.UniqueConstraint("model_key", name="uq_model_version_key"),
    )
    for name, columns in (
        ("ix_model_version_project_id", ["project_id"]),
        ("ix_model_version_algorithm_id", ["algorithm_id"]),
        ("ix_model_version_dataset_version_id", ["dataset_version_id"]),
        ("ix_model_version_training_job_id", ["training_job_id"]),
        ("ix_model_version_parent_model_version_id", ["parent_model_version_id"]),
        ("ix_model_version_published_catalog_id", ["published_catalog_id"]),
        ("ix_model_version_status", ["status"]),
        ("idx_model_version_project_status", ["project_id", "status"]),
        ("idx_model_version_dataset", ["dataset_version_id"]),
    ):
        op.create_index(name, "model_version", columns, unique=False)

    op.create_table(
        "model_artifact",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("chip_type", sa.String(length=64), nullable=True),
        sa.Column("precision", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_version_id"], ["model_version.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_version_id", "artifact_type", "sha256", name="uq_model_artifact_hash"
        ),
    )
    op.create_index(
        "ix_model_artifact_model_version_id",
        "model_artifact",
        ["model_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_model_artifact_status", "model_artifact", ["status"], unique=False
    )
    op.create_index(
        "idx_model_artifact_model_status",
        "model_artifact",
        ["model_version_id", "status"],
        unique=False,
    )

    op.create_table(
        "model_quality_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("gate_type", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("evidence_uri", sa.String(length=512), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("evaluator", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_version_id"], ["model_version.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_version_id",
            "gate_type",
            "revision",
            name="uq_model_quality_gate_revision",
        ),
    )
    op.create_index(
        "ix_model_quality_evidence_model_version_id",
        "model_quality_evidence",
        ["model_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_model_quality_evidence_status",
        "model_quality_evidence",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_model_quality_model_gate",
        "model_quality_evidence",
        ["model_version_id", "gate_type", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_model_quality_model_gate", table_name="model_quality_evidence")
    op.drop_index(
        "ix_model_quality_evidence_status", table_name="model_quality_evidence"
    )
    op.drop_index(
        "ix_model_quality_evidence_model_version_id",
        table_name="model_quality_evidence",
    )
    op.drop_table("model_quality_evidence")
    op.drop_index("idx_model_artifact_model_status", table_name="model_artifact")
    op.drop_index("ix_model_artifact_status", table_name="model_artifact")
    op.drop_index("ix_model_artifact_model_version_id", table_name="model_artifact")
    op.drop_table("model_artifact")
    for name in (
        "idx_model_version_dataset",
        "idx_model_version_project_status",
        "ix_model_version_status",
        "ix_model_version_published_catalog_id",
        "ix_model_version_parent_model_version_id",
        "ix_model_version_training_job_id",
        "ix_model_version_dataset_version_id",
        "ix_model_version_algorithm_id",
        "ix_model_version_project_id",
    ):
        op.drop_index(name, table_name="model_version")
    op.drop_table("model_version")
