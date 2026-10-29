"""Add independent labels and versioned dataset contracts.

Revision ID: w5x6y7z8a9b0
Revises: v4w5x6y7z8a9
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "w5x6y7z8a9b0"
down_revision = "v4w5x6y7z8a9"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(inspect(bind).get_table_names())


def _columns(bind, table):
    return {row["name"] for row in inspect(bind).get_columns(table)}


def _column_details(bind, table):
    return {row["name"]: row for row in inspect(bind).get_columns(table)}


def _add_columns(bind, table, columns):
    existing = _columns(bind, table)
    with op.batch_alter_table(table) as batch:
        for column in columns:
            if column.name not in existing:
                batch.add_column(column)


def _backfill_required_json_columns(bind, table, defaults):
    """Populate required JSON text columns before enforcing NOT NULL.

    MySQL rejects defaults on TEXT columns. Adding these columns as nullable,
    backfilling existing rows, and then tightening nullability works on both
    MySQL and SQLite and remains safe after a partially applied MySQL migration.
    """
    metadata = sa.MetaData()
    reflected = sa.Table(table, metadata, autoload_with=bind)
    for name, value in defaults.items():
        column = reflected.c[name]
        bind.execute(reflected.update().where(column.is_(None)).values({name: value}))

    details = _column_details(bind, table)
    nullable_columns = [
        name for name in defaults if bool(details.get(name, {}).get("nullable", True))
    ]
    if nullable_columns:
        with op.batch_alter_table(table) as batch:
            for name in nullable_columns:
                batch.alter_column(name, existing_type=sa.Text(), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "annotation_label" not in tables:
        op.create_table(
            "annotation_label",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("label_key", sa.String(128), nullable=False),
            sa.Column("label_value", sa.String(128), nullable=False),
            sa.Column("normalized_label_value", sa.String(128), nullable=False),
            sa.Column(
                "annotation_kind", sa.String(32), nullable=False, server_default="bbox"
            ),
            sa.Column("color", sa.String(32), nullable=False, server_default="#1677ff"),
            sa.Column("description", sa.String(512), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "normalized_label_value", name="uq_annotation_label_normalized_value"
            ),
        )
        op.create_index("ix_annotation_label_status", "annotation_label", ["status"])

    if "annotation_label_alias" not in tables:
        op.create_table(
            "annotation_label_alias",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("source_system", sa.String(64), nullable=False),
            sa.Column("external_label_value", sa.String(128), nullable=False),
            sa.Column("normalized_external_value", sa.String(128), nullable=False),
            sa.Column("label_id", sa.Integer(), nullable=False),
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
            sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["label_id"], ["annotation_label.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_system",
                "normalized_external_value",
                name="uq_annotation_label_alias_source_value",
            ),
        )
        op.create_index(
            "ix_annotation_label_alias_label", "annotation_label_alias", ["label_id"]
        )

    if "label_preset_label" not in tables:
        op.create_table(
            "label_preset_label",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("preset_id", sa.Integer(), nullable=False),
            sa.Column("label_id", sa.Integer(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["preset_id"], ["label_preset.id"]),
            sa.ForeignKeyConstraint(["label_id"], ["annotation_label.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("preset_id", "label_id", name="uq_label_preset_label"),
        )
        op.create_index(
            "ix_label_preset_label_preset", "label_preset_label", ["preset_id"]
        )
        op.create_index(
            "ix_label_preset_label_label", "label_preset_label", ["label_id"]
        )

    if "dataset_category" not in tables:
        op.create_table(
            "dataset_category",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("category_key", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(512), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("category_key", name="uq_dataset_category_key"),
        )

    if "training_recipe" not in tables:
        op.create_table(
            "training_recipe",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("recipe_key", sa.String(128), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("job_type", sa.String(64), nullable=False),
            sa.Column("model_family", sa.String(64), nullable=False),
            sa.Column(
                "annotation_kind", sa.String(32), nullable=False, server_default="bbox"
            ),
            sa.Column(
                "component_contract_json",
                sa.Text(),
                nullable=False,
            ),
            sa.Column(
                "default_hyperparams_json",
                sa.Text(),
                nullable=False,
            ),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "recipe_key", "version", name="uq_training_recipe_key_version"
            ),
        )

    if "runtime_publish_template" not in tables:
        op.create_table(
            "runtime_publish_template",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("template_key", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column(
                "environment_id",
                sa.String(128),
                nullable=False,
                server_default="default",
            ),
            sa.Column("runtime_profile", sa.String(128), nullable=False),
            sa.Column(
                "interface_version", sa.Integer(), nullable=False, server_default="1"
            ),
            sa.Column("aicm_model_set_id", sa.String(128), nullable=True),
            sa.Column("expected_model_alg_type", sa.String(128), nullable=True),
            sa.Column(
                "capability_contract_json",
                sa.Text(),
                nullable=False,
            ),
            sa.Column("template_sha256", sa.String(64), nullable=True),
            sa.Column(
                "is_default", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "template_key",
                "environment_id",
                "interface_version",
                name="uq_runtime_publish_template_contract",
            ),
        )

    if "taxonomy_audit_log" not in tables:
        op.create_table(
            "taxonomy_audit_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("entity_type", sa.String(64), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("change_reason", sa.String(512), nullable=True),
            sa.Column("request_id", sa.String(128), nullable=True),
            sa.Column("source_ip", sa.String(64), nullable=True),
            sa.Column("changed_by", sa.Integer(), nullable=True),
            sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["changed_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_taxonomy_audit_entity",
            "taxonomy_audit_log",
            ["entity_type", "entity_id"],
        )

    if "third_party_request_receipt" not in tables:
        op.create_table(
            "third_party_request_receipt",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("source_system", sa.String(64), nullable=False),
            sa.Column("nonce", sa.String(128), nullable=False),
            sa.Column("request_method", sa.String(16), nullable=False),
            sa.Column("request_path", sa.String(512), nullable=False),
            sa.Column("request_sha256", sa.String(64), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_system", "nonce", name="uq_third_party_request_source_nonce"
            ),
        )
        op.create_index(
            "ix_third_party_request_source_received",
            "third_party_request_receipt",
            ["source_system", "received_at"],
        )

    if "task_annotation_contract" not in tables:
        op.create_table(
            "task_annotation_contract",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column(
                "annotation_kind", sa.String(32), nullable=False, server_default="bbox"
            ),
            sa.Column("label_ids_json", sa.Text(), nullable=False),
            sa.Column("labels_snapshot_json", sa.Text(), nullable=False),
            sa.Column("label_set_signature", sa.String(64), nullable=False),
            sa.Column("class_order_signature", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("source_preset_id", sa.Integer(), nullable=True),
            sa.Column("parent_contract_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["source_preset_id"], ["label_preset.id"]),
            sa.ForeignKeyConstraint(
                ["parent_contract_id"], ["task_annotation_contract.id"]
            ),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "task_id", "revision", name="uq_task_annotation_contract_revision"
            ),
        )
        op.create_index(
            "ix_task_annotation_contract_task_status",
            "task_annotation_contract",
            ["task_id", "status"],
        )

    if "task_annotation_label" not in tables:
        op.create_table(
            "task_annotation_label",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("contract_id", sa.Integer(), nullable=False),
            sa.Column("label_id", sa.Integer(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.ForeignKeyConstraint(["contract_id"], ["task_annotation_contract.id"]),
            sa.ForeignKeyConstraint(["label_id"], ["annotation_label.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "contract_id",
                "label_id",
                name="uq_task_annotation_label_contract_label",
            ),
        )
        op.create_index(
            "ix_task_annotation_label_lookup",
            "task_annotation_label",
            ["label_id", "task_id", "status"],
        )

    if "sample_annotation_scope" not in tables:
        op.create_table(
            "sample_annotation_scope",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("contract_id", sa.Integer(), nullable=False),
            sa.Column(
                "completion_status",
                sa.String(32),
                nullable=False,
                server_default="unprocessed",
            ),
            sa.Column(
                "explicit_negative",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "negative_label_ids_json",
                sa.Text(),
                nullable=False,
            ),
            sa.Column("annotation_sha256", sa.String(64), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["sample_id"], ["task_sample.id"]),
            sa.ForeignKeyConstraint(["contract_id"], ["task_annotation_contract.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("sample_id", name="uq_sample_annotation_scope_sample"),
        )
        op.create_index(
            "ix_sample_annotation_scope_contract_status",
            "sample_annotation_scope",
            ["contract_id", "completion_status"],
        )

    _add_columns(
        bind,
        "task",
        [sa.Column("current_annotation_contract_id", sa.Integer(), nullable=True)],
    )
    _add_columns(
        bind,
        "training_task",
        [
            sa.Column("dataset_category_id", sa.Integer(), nullable=True),
            sa.Column("dataset_category_snapshot_json", sa.Text(), nullable=True),
            sa.Column(
                "selected_label_ids_json",
                sa.Text(),
                nullable=True,
            ),
            sa.Column(
                "class_names_snapshot_json",
                sa.Text(),
                nullable=True,
            ),
            sa.Column("label_set_signature", sa.String(64), nullable=True),
            sa.Column("class_order_signature", sa.String(64), nullable=True),
            sa.Column("source_sample_snapshot_json", sa.Text(), nullable=True),
            sa.Column("training_recipe_key", sa.String(128), nullable=True),
            sa.Column("training_recipe_version", sa.Integer(), nullable=True),
            sa.Column("runtime_profile", sa.String(128), nullable=True),
            sa.Column("runtime_interface_version", sa.Integer(), nullable=True),
            sa.Column("quality_policy_snapshot_json", sa.Text(), nullable=True),
        ],
    )
    _backfill_required_json_columns(
        bind,
        "training_task",
        {
            "selected_label_ids_json": "[]",
            "class_names_snapshot_json": "[]",
        },
    )
    _add_columns(
        bind,
        "yolo_train_job",
        [
            sa.Column("dataset_category_id", sa.Integer(), nullable=True),
            sa.Column("class_names_json", sa.Text(), nullable=True),
            sa.Column("label_set_signature", sa.String(64), nullable=True),
            sa.Column("class_order_signature", sa.String(64), nullable=True),
            sa.Column("data_yaml_sha256", sa.String(64), nullable=True),
            sa.Column("dataset_content_signature", sa.String(64), nullable=True),
            sa.Column("source_sample_manifest_json", sa.Text(), nullable=True),
            sa.Column("training_recipe_key", sa.String(128), nullable=True),
            sa.Column("training_recipe_version", sa.Integer(), nullable=True),
            sa.Column("quality_policy_snapshot_json", sa.Text(), nullable=True),
            sa.Column("runtime_profile", sa.String(128), nullable=True),
            sa.Column("runtime_interface_version", sa.Integer(), nullable=True),
        ],
    )
    _add_columns(
        bind,
        "yolo_preannotate_job",
        [
            sa.Column("task_contract_id", sa.Integer(), nullable=True),
            sa.Column("task_contract_revision", sa.Integer(), nullable=True),
            sa.Column("labels_snapshot_json", sa.Text(), nullable=True),
            sa.Column("label_set_signature", sa.String(64), nullable=True),
        ],
    )
    _add_columns(
        bind,
        "strategy_task_integration",
        [
            sa.Column("dataset_category_id", sa.Integer(), nullable=True),
            sa.Column("request_payload_json", sa.Text(), nullable=True),
            sa.Column("request_sha256", sa.String(64), nullable=True),
        ],
    )

    # Backfill the canonical label library and preset associations without changing legacy rows.
    metadata = sa.MetaData()
    legacy = sa.Table("label_preset_item", metadata, autoload_with=bind)
    labels = sa.Table("annotation_label", metadata, autoload_with=bind)
    relations = sa.Table("label_preset_label", metadata, autoload_with=bind)
    rows = bind.execute(sa.select(legacy).order_by(legacy.c.id)).mappings().all()
    canonical = {
        str(row.normalized_label_value): int(row.id)
        for row in bind.execute(
            sa.select(labels.c.id, labels.c.normalized_label_value)
        ).all()
    }
    now = datetime.now()
    for row in rows:
        value = str(row.get("label_value") or "").strip()
        normalized = value.lower()
        if not normalized:
            continue
        label_id = canonical.get(normalized)
        if label_id is None:
            result = bind.execute(
                labels.insert().values(
                    label_key=str(row.get("label_key") or value).strip(),
                    label_value=value,
                    normalized_label_value=normalized,
                    annotation_kind="bbox",
                    color=str(row.get("color") or "#1677ff"),
                    status="active",
                    version=1,
                    sort_order=int(row.get("sort_order") or 0),
                    created_at=now,
                    updated_at=now,
                )
            )
            label_id = int(result.inserted_primary_key[0])
            canonical[normalized] = label_id
        exists = bind.execute(
            sa.select(relations.c.id).where(
                relations.c.preset_id == int(row["preset_id"]),
                relations.c.label_id == label_id,
            )
        ).first()
        if not exists:
            bind.execute(
                relations.insert().values(
                    preset_id=int(row["preset_id"]),
                    label_id=label_id,
                    sort_order=int(row.get("sort_order") or 0),
                    created_at=now,
                )
            )

    recipes = sa.Table("training_recipe", metadata, autoload_with=bind)
    if not bind.execute(sa.select(recipes.c.id).limit(1)).first():
        bind.execute(
            recipes.insert(),
            [
                {
                    "recipe_key": "yolov11_detection",
                    "version": 1,
                    "name": "YOLOv11 通用检测",
                    "job_type": "yolo_detection",
                    "model_family": "yolov11",
                    "annotation_kind": "bbox",
                    "component_contract_json": "{}",
                    "default_hyperparams_json": "{}",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "recipe_key": "plate_recognition_v1",
                    "version": 1,
                    "name": "车牌复合识别",
                    "job_type": "plate_recognition",
                    "model_family": "plate_recognition",
                    "annotation_kind": "bbox",
                    "component_contract_json": "{}",
                    "default_hyperparams_json": "{}",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
    templates = sa.Table("runtime_publish_template", metadata, autoload_with=bind)
    if not bind.execute(sa.select(templates.c.id).limit(1)).first():
        bind.execute(
            templates.insert().values(
                template_key="yolov11_detection",
                name="YOLOv11 通用检测运行时",
                environment_id="default",
                runtime_profile="yolov11_detection",
                interface_version=1,
                expected_model_alg_type="yoloV11",
                capability_contract_json="{}",
                is_default=True,
                status="active",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table, columns in (
        (
            "strategy_task_integration",
            ["request_sha256", "request_payload_json", "dataset_category_id"],
        ),
        (
            "yolo_preannotate_job",
            [
                "label_set_signature",
                "labels_snapshot_json",
                "task_contract_revision",
                "task_contract_id",
            ],
        ),
        (
            "yolo_train_job",
            [
                "runtime_interface_version",
                "runtime_profile",
                "quality_policy_snapshot_json",
                "training_recipe_version",
                "training_recipe_key",
                "source_sample_manifest_json",
                "dataset_content_signature",
                "data_yaml_sha256",
                "class_order_signature",
                "label_set_signature",
                "class_names_json",
                "dataset_category_id",
            ],
        ),
        (
            "training_task",
            [
                "quality_policy_snapshot_json",
                "runtime_interface_version",
                "runtime_profile",
                "training_recipe_version",
                "training_recipe_key",
                "source_sample_snapshot_json",
                "class_order_signature",
                "label_set_signature",
                "class_names_snapshot_json",
                "selected_label_ids_json",
                "dataset_category_snapshot_json",
                "dataset_category_id",
            ],
        ),
        ("task", ["current_annotation_contract_id"]),
    ):
        existing = _columns(bind, table)
        dropping = existing.intersection(columns)
        for index in inspect(bind).get_indexes(table):
            indexed_columns = set(index.get("column_names") or [])
            if indexed_columns.intersection(dropping):
                op.drop_index(index["name"], table_name=table)
        with op.batch_alter_table(table) as batch:
            for name in columns:
                if name in existing:
                    batch.drop_column(name)
    for table in [
        "sample_annotation_scope",
        "task_annotation_label",
        "task_annotation_contract",
        "third_party_request_receipt",
        "taxonomy_audit_log",
        "runtime_publish_template",
        "training_recipe",
        "dataset_category",
        "label_preset_label",
        "annotation_label_alias",
        "annotation_label",
    ]:
        if table in _tables(bind):
            op.drop_table(table)
