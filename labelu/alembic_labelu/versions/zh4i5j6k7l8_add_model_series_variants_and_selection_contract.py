"""Add model-series/branch lineage and immutable selection contracts."""

from alembic import op
import sqlalchemy as sa


revision = "zh4i5j6k7l8"
down_revision = "zg3h4i5j6k7l"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def _add_column(bind, table, column):
    if table in _tables(bind) and column.name not in _columns(bind, table):
        op.add_column(table, column)


def _add_index(bind, name, table, columns):
    if table in _tables(bind) and name not in _indexes(bind, table):
        op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "model_series" not in tables:
        op.create_table(
            "model_series",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("model_family", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "name", name="uq_model_series_owner_name"),
        )
        op.create_index("ix_model_series_owner_id", "model_series", ["owner_id"], unique=False)
        op.create_index("ix_model_series_model_family", "model_series", ["model_family"], unique=False)
        op.create_index("ix_model_series_status", "model_series", ["status"], unique=False)
        op.create_index("idx_model_series_owner_status", "model_series", ["owner_id", "status"], unique=False)
        op.create_index("idx_model_series_family_status", "model_series", ["model_family", "status"], unique=False)

    # Refresh the table set after creating model_series.
    tables = _tables(bind)
    if "model_variant" not in tables:
        op.create_table(
            "model_variant",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("series_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("branch_key", sa.String(length=64), nullable=False),
            sa.Column("selection_contract_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("selection_contract_sha256", sa.String(length=64), nullable=False),
            sa.Column("class_contract_json", sa.Text(), nullable=True),
            sa.Column("recipe_json", sa.Text(), nullable=True),
            sa.Column("parent_model_version_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["series_id"], ["model_series.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("series_id", "branch_key", name="uq_model_variant_series_branch"),
        )
        op.create_index("ix_model_variant_series_id", "model_variant", ["series_id"], unique=False)
        op.create_index("ix_model_variant_branch_key", "model_variant", ["branch_key"], unique=False)
        op.create_index("ix_model_variant_selection_contract_sha256", "model_variant", ["selection_contract_sha256"], unique=False)
        op.create_index("ix_model_variant_parent_model_version_id", "model_variant", ["parent_model_version_id"], unique=False)
        op.create_index("ix_model_variant_status", "model_variant", ["status"], unique=False)
        op.create_index("ix_model_variant_created_by", "model_variant", ["created_by"], unique=False)
        op.create_index("ix_model_variant_deleted_at", "model_variant", ["deleted_at"], unique=False)
        op.create_index("idx_model_variant_series_status", "model_variant", ["series_id", "status"], unique=False)

    # All links are nullable so historical jobs/models remain readable during
    # the rolling upgrade.  SQLite cannot add foreign keys with ALTER TABLE;
    # ORM metadata enforces them for new databases and MySQL gets constraints
    # in a follow-up deployment migration if required by the operator.
    for table, columns in {
        "training_task": (
            sa.Column("series_id", sa.Integer(), nullable=True),
            sa.Column("variant_id", sa.Integer(), nullable=True),
            sa.Column("branch_key", sa.String(length=64), nullable=True),
            sa.Column("selection_contract_sha256", sa.String(length=64), nullable=True),
            sa.Column("selection_contract_json", sa.Text(), nullable=True),
            sa.Column("sample_conditions_json", sa.Text(), nullable=True),
        ),
        "yolo_train_job": (
            sa.Column("series_id", sa.Integer(), nullable=True),
            sa.Column("variant_id", sa.Integer(), nullable=True),
            sa.Column("parent_model_version_id", sa.Integer(), nullable=True),
            sa.Column("parent_training_job_id", sa.Integer(), nullable=True),
            sa.Column("branch_key", sa.String(length=64), nullable=True),
            sa.Column("selection_contract_sha256", sa.String(length=64), nullable=True),
            sa.Column("selection_contract_json", sa.Text(), nullable=True),
            sa.Column("sample_conditions_json", sa.Text(), nullable=True),
            sa.Column("inheritance_mode", sa.String(length=32), nullable=True),
        ),
        "model_version": (
            sa.Column("series_id", sa.Integer(), nullable=True),
            sa.Column("variant_id", sa.Integer(), nullable=True),
            sa.Column("inheritance_mode", sa.String(length=32), nullable=True),
            sa.Column("branch_key", sa.String(length=64), nullable=True),
            sa.Column("selection_contract_sha256", sa.String(length=64), nullable=True),
            sa.Column("selection_contract_json", sa.Text(), nullable=True),
            sa.Column("parent_training_job_id", sa.Integer(), nullable=True),
        ),
    }.items():
        for column in columns:
            _add_column(bind, table, column)

    for table, entries in {
        "training_task": (
            ("ix_training_task_series_id", ["series_id"]),
            ("ix_training_task_variant_id", ["variant_id"]),
            ("ix_training_task_branch_key", ["branch_key"]),
            ("ix_training_task_selection_contract_sha256", ["selection_contract_sha256"]),
        ),
        "yolo_train_job": (
            ("ix_yolo_train_job_series_id", ["series_id"]),
            ("ix_yolo_train_job_variant_id", ["variant_id"]),
            ("ix_yolo_train_job_parent_model_version_id", ["parent_model_version_id"]),
            ("ix_yolo_train_job_parent_training_job_id", ["parent_training_job_id"]),
            ("ix_yolo_train_job_branch_key", ["branch_key"]),
            ("ix_yolo_train_job_selection_contract_sha256", ["selection_contract_sha256"]),
        ),
        "model_version": (
            ("ix_model_version_series_id", ["series_id"]),
            ("ix_model_version_variant_id", ["variant_id"]),
            ("ix_model_version_branch_key", ["branch_key"]),
            ("ix_model_version_selection_contract_sha256", ["selection_contract_sha256"]),
            ("ix_model_version_parent_training_job_id", ["parent_training_job_id"]),
        ),
    }.items():
        for name, columns in entries:
            _add_index(bind, name, table, columns)


def downgrade() -> None:
    raise RuntimeError(
        "模型系列和分支合同是新增的审计血缘；回退会丢失训练分支信息，请从备份恢复。"
    )

