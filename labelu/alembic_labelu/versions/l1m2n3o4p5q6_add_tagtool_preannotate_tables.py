"""add tagtool classification model and preannotate job tables

Revision ID: l1m2n3o4p5q6
Revises: k1l2m3n4o5p6
Create Date: 2026-05-25

"""

from alembic import op
import sqlalchemy as sa

revision = "l1m2n3o4p5q6"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "tagtool_classification_model" not in tables:
        op.create_table(
            "tagtool_classification_model",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("display_name", sa.String(length=128), nullable=True),
            sa.Column("category", sa.String(length=128), nullable=True),
            sa.Column("original_filename", sa.String(length=256), nullable=True),
            sa.Column("weights_rel_path", sa.String(length=512), nullable=False),
            sa.Column("label_map_json", sa.Text(), nullable=False),
            sa.Column("file_size", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tagtool_classification_model_id", "tagtool_classification_model", ["id"])
        op.create_index("ix_tagtool_classification_model_user_id", "tagtool_classification_model", ["user_id"])

    if "tagtool_preannotate_job" not in tables:
        op.create_table(
            "tagtool_preannotate_job",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=True),
            sa.Column("processed_count", sa.Integer(), nullable=True),
            sa.Column("success_count", sa.Integer(), nullable=True),
            sa.Column("failed_count", sa.Integer(), nullable=True),
            sa.Column("model_kind", sa.String(length=32), nullable=False),
            sa.Column("model_id", sa.Integer(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tagtool_preannotate_job_id", "tagtool_preannotate_job", ["id"])
        op.create_index("ix_tagtool_preannotate_job_task_id", "tagtool_preannotate_job", ["task_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "tagtool_preannotate_job" in tables:
        op.drop_index("ix_tagtool_preannotate_job_task_id", table_name="tagtool_preannotate_job")
        op.drop_index("ix_tagtool_preannotate_job_id", table_name="tagtool_preannotate_job")
        op.drop_table("tagtool_preannotate_job")
    if "tagtool_classification_model" in tables:
        op.drop_index("ix_tagtool_classification_model_user_id", table_name="tagtool_classification_model")
        op.drop_index("ix_tagtool_classification_model_id", table_name="tagtool_classification_model")
        op.drop_table("tagtool_classification_model")
