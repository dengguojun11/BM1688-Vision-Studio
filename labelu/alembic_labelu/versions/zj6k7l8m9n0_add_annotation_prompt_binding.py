"""Add versioned AI prompt bindings for annotation contracts.

The prompt is an executable projection of an immutable label contract.  Keep
the projection in its own table so editing or publishing a prompt never
mutates the contract itself and so workers can resolve an exact revision.
"""

from alembic import op
import sqlalchemy as sa


revision = "zj6k7l8m9n0"
down_revision = "zi5j6k7l8m9"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table):
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def _create_index_if_missing(bind, name, table, columns, *, unique=False):
    if name not in _indexes(bind, table):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    if "annotation_prompt_binding" not in _tables(bind):
        op.create_table(
            "annotation_prompt_binding",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("dataset_id", sa.Integer(), nullable=True),
            sa.Column("dataset_label_contract_id", sa.Integer(), nullable=True),
            sa.Column("task_annotation_contract_id", sa.Integer(), nullable=True),
            sa.Column("contract_revision", sa.Integer(), nullable=False),
            sa.Column("label_set_signature", sa.String(length=64), nullable=False),
            sa.Column("class_order_signature", sa.String(length=64), nullable=False),
            sa.Column("operation", sa.String(length=64), nullable=False, server_default="bbox_detection"),
            sa.Column("provider_key", sa.String(length=64), nullable=False, server_default="volcengine"),
            sa.Column("model_name", sa.String(length=256), nullable=True),
            sa.Column("profile_key", sa.String(length=128), nullable=False, server_default="annotation_bbox_v1"),
            sa.Column("profile_version", sa.String(length=32), nullable=False, server_default="1"),
            sa.Column("compiler_version", sa.String(length=32), nullable=False, server_default="1"),
            # Text columns intentionally have no server default for MySQL
            # compatibility.  The application writes explicit JSON values.
            sa.Column("prompt_spec_json", sa.Text(), nullable=False),
            sa.Column("generated_prompt", sa.Text(), nullable=False),
            sa.Column("edited_prompt", sa.Text(), nullable=True),
            sa.Column("effective_prompt", sa.Text(), nullable=False),
            sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
            sa.Column("warnings_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("published_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"]),
            sa.ForeignKeyConstraint(
                ["dataset_label_contract_id"], ["dataset_label_contract.id"]
            ),
            sa.ForeignKeyConstraint(
                ["task_annotation_contract_id"], ["task_annotation_contract.id"]
            ),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["published_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    # Keep migration replay safe for databases where the table was created by
    # an application bootstrap before Alembic was run.
    for name, columns in (
        ("ix_annotation_prompt_binding_id", ["id"]),
        ("ix_annotation_prompt_binding_dataset_id", ["dataset_id"]),
        ("ix_annotation_prompt_binding_dataset_label_contract_id", ["dataset_label_contract_id"]),
        ("ix_annotation_prompt_binding_task_annotation_contract_id", ["task_annotation_contract_id"]),
        ("ix_annotation_prompt_binding_label_set_signature", ["label_set_signature"]),
        ("ix_annotation_prompt_binding_class_order_signature", ["class_order_signature"]),
        ("ix_annotation_prompt_binding_operation", ["operation"]),
        ("ix_annotation_prompt_binding_provider_key", ["provider_key"]),
        ("ix_annotation_prompt_binding_prompt_sha256", ["prompt_sha256"]),
        ("ix_annotation_prompt_binding_status", ["status"]),
        (
            "ix_annotation_prompt_binding_dataset_contract",
            ["dataset_label_contract_id", "contract_revision", "operation", "status"],
        ),
        (
            "ix_annotation_prompt_binding_task_contract",
            ["task_annotation_contract_id", "contract_revision", "operation", "status"],
        ),
    ):
        _create_index_if_missing(bind, name, "annotation_prompt_binding", columns)


def downgrade() -> None:
    bind = op.get_bind()
    if "annotation_prompt_binding" in _tables(bind):
        op.drop_table("annotation_prompt_binding")

