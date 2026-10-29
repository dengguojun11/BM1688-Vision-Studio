"""Add the canonical dataset label contract and task lineage bindings."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "z3a4b5c6d7e8"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(inspect(bind).get_table_names())


def _columns(bind, table_name):
    if table_name not in _tables(bind):
        return set()
    return {item["name"] for item in inspect(bind).get_columns(table_name)}


def _indexes(bind, table_name):
    if table_name not in _tables(bind):
        return set()
    return {item["name"] for item in inspect(bind).get_indexes(table_name)}


def _add_column_if_missing(bind, table_name, column):
    if table_name not in _tables(bind):
        return
    if column.name not in _columns(bind, table_name):
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(column)


def _create_index_if_missing(bind, name, table_name, columns):
    if name not in _indexes(bind, table_name):
        op.create_index(name, table_name, columns, unique=False)


def upgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "dataset" not in tables:
        return

    if "dataset_label_contract" not in tables:
        op.create_table(
            "dataset_label_contract",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("dataset_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("annotation_kind", sa.String(length=32), nullable=False, server_default="bbox"),
            # MySQL does not allow server defaults on TEXT columns.  The
            # application always writes these JSON snapshots explicitly, so
            # keep the columns required without a database-level default.
            sa.Column("label_ids_json", sa.Text(), nullable=False),
            sa.Column("labels_snapshot_json", sa.Text(), nullable=False),
            sa.Column("label_set_signature", sa.String(length=64), nullable=False),
            sa.Column("class_order_signature", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("frozen_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["frozen_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "dataset_id", "revision", name="uq_dataset_label_contract_revision"
            ),
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_dataset_id",
            "dataset_label_contract",
            ["dataset_id"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_project_id",
            "dataset_label_contract",
            ["project_id"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_label_set_signature",
            "dataset_label_contract",
            ["label_set_signature"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_class_order_signature",
            "dataset_label_contract",
            ["class_order_signature"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_status",
            "dataset_label_contract",
            ["status"],
        )
        _create_index_if_missing(
            bind,
            "idx_dataset_label_contract_dataset_status",
            "dataset_label_contract",
            ["dataset_id", "status"],
        )

    if "dataset_label_contract_item" not in _tables(bind):
        op.create_table(
            "dataset_label_contract_item",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("dataset_id", sa.Integer(), nullable=False),
            sa.Column("contract_id", sa.Integer(), nullable=False),
            sa.Column("label_id", sa.Integer(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["dataset_id"], ["dataset.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["contract_id"],
                ["dataset_label_contract.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["label_id"], ["annotation_label.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "contract_id",
                "label_id",
                name="uq_dataset_label_contract_item_label",
            ),
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_item_dataset_id",
            "dataset_label_contract_item",
            ["dataset_id"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_item_contract_id",
            "dataset_label_contract_item",
            ["contract_id"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_item_label_id",
            "dataset_label_contract_item",
            ["label_id"],
        )
        _create_index_if_missing(
            bind,
            "ix_dataset_label_contract_item_status",
            "dataset_label_contract_item",
            ["status"],
        )
        _create_index_if_missing(
            bind,
            "idx_dataset_label_contract_item_contract_order",
            "dataset_label_contract_item",
            ["contract_id", "sort_order"],
        )

    _add_column_if_missing(
        bind,
        "dataset",
        sa.Column("dataset_label_contract_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "dataset_version",
        sa.Column("dataset_label_contract_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "dataset_version",
        sa.Column("dataset_contract_revision", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "dataset_version",
        sa.Column("labels_snapshot_json", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        bind, "task", sa.Column("dataset_id", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        bind,
        "task",
        sa.Column("dataset_label_contract_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "task",
        sa.Column("task_label_scope_mode", sa.String(length=16), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "task_annotation_contract",
        sa.Column("dataset_label_contract_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "task_annotation_contract",
        sa.Column("label_scope_mode", sa.String(length=16), nullable=True),
    )

    _create_index_if_missing(bind, "ix_dataset_dataset_label_contract_id", "dataset", ["dataset_label_contract_id"])
    _create_index_if_missing(
        bind,
        "ix_dataset_version_dataset_label_contract_id",
        "dataset_version",
        ["dataset_label_contract_id"],
    )
    _create_index_if_missing(bind, "ix_task_dataset_id", "task", ["dataset_id"])
    _create_index_if_missing(
        bind,
        "ix_task_dataset_label_contract_id",
        "task",
        ["dataset_label_contract_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_task_annotation_contract_dataset_label_contract_id",
        "task_annotation_contract",
        ["dataset_label_contract_id"],
    )

    if "task" in _tables(bind) and "task_label_scope_mode" in _columns(bind, "task"):
        bind.execute(
            text(
                "UPDATE task SET task_label_scope_mode = 'FULL' "
                "WHERE task_label_scope_mode IS NULL"
            )
        )
    if (
        "task_annotation_contract" in _tables(bind)
        and "label_scope_mode" in _columns(bind, "task_annotation_contract")
    ):
        bind.execute(
            text(
                "UPDATE task_annotation_contract SET label_scope_mode = 'FULL' "
                "WHERE label_scope_mode IS NULL"
            )
        )


def downgrade():
    bind = op.get_bind()
    for index_name, table_name in (
        ("ix_task_annotation_contract_dataset_label_contract_id", "task_annotation_contract"),
        ("ix_task_dataset_label_contract_id", "task"),
        ("ix_task_dataset_id", "task"),
        ("ix_dataset_version_dataset_label_contract_id", "dataset_version"),
        ("ix_dataset_dataset_label_contract_id", "dataset"),
    ):
        if index_name in _indexes(bind, table_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name, columns in (
        (
            "task_annotation_contract",
            ("label_scope_mode", "dataset_label_contract_id"),
        ),
        ("task", ("task_label_scope_mode", "dataset_label_contract_id", "dataset_id")),
        (
            "dataset_version",
            ("labels_snapshot_json", "dataset_contract_revision", "dataset_label_contract_id"),
        ),
        ("dataset", ("dataset_label_contract_id",)),
    ):
        if table_name not in _tables(bind):
            continue
        existing = _columns(bind, table_name)
        with op.batch_alter_table(table_name) as batch:
            for column in columns:
                if column in existing:
                    batch.drop_column(column)

    if "dataset_label_contract_item" in _tables(bind):
        op.drop_table("dataset_label_contract_item")
    if "dataset_label_contract" in _tables(bind):
        op.drop_table("dataset_label_contract")
