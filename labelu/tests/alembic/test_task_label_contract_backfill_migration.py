from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "z8a9b0c1d2e3_backfill_task_label_contracts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "task_label_contract_backfill_migration", migration_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_rect_tool_config_is_backfilled_idempotently():
    migration = _load_migration_module()
    metadata = sa.MetaData()
    sa.Table(
        "task",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("config", sa.Text),
        sa.Column("deleted_at", sa.DateTime),
        sa.Column("current_annotation_contract_id", sa.Integer),
        sa.Column("created_by", sa.Integer),
    )
    sa.Table(
        "annotation_label",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label_key", sa.String(128), nullable=False),
        sa.Column("label_value", sa.String(128), nullable=False),
        sa.Column("normalized_label_value", sa.String(128), nullable=False),
        sa.Column("color", sa.String(32), nullable=False),
        sa.Column("annotation_kind", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("attributes_json", sa.Text, nullable=False),
        sa.Column("deleted_at", sa.DateTime),
    )
    sa.Table(
        "task_annotation_contract",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("annotation_kind", sa.String(32), nullable=False),
        sa.Column("label_ids_json", sa.Text, nullable=False),
        sa.Column("labels_snapshot_json", sa.Text, nullable=False),
        sa.Column("label_set_signature", sa.String(64), nullable=False),
        sa.Column("class_order_signature", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_preset_id", sa.Integer),
        sa.Column("parent_contract_id", sa.Integer),
        sa.Column("created_by", sa.Integer),
        sa.Column("created_at", sa.DateTime),
    )
    sa.Table(
        "task_annotation_label",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, nullable=False),
        sa.Column("contract_id", sa.Integer, nullable=False),
        sa.Column("label_id", sa.Integer, nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime),
    )

    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {"key": "车", "value": "car", "color": "#1677ff"}
                    ]
                },
            }
        ]
    }
    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "INSERT INTO task (id, config, created_by) VALUES (1, :config, 7)"
            ),
            {"config": json.dumps(config, ensure_ascii=False)},
        )
        bind.execute(
            sa.text(
                "INSERT INTO annotation_label "
                "(id, label_key, label_value, normalized_label_value, color, "
                "annotation_kind, version, attributes_json) "
                "VALUES (3, '车', 'car', 'car', '#1677ff', 'bbox', 1, '[]')"
            )
        )
        assert migration._backfill_task_contracts(bind) == 1
        assert migration._backfill_task_contracts(bind) == 0
        contract = bind.execute(
            sa.text(
                "SELECT id, label_ids_json, class_order_signature "
                "FROM task_annotation_contract"
            )
        ).one()
        assert json.loads(contract.label_ids_json) == [3]
        assert bind.execute(sa.text("SELECT current_annotation_contract_id FROM task")).scalar() == contract.id
        assert bind.execute(sa.text("SELECT COUNT(*) FROM task_annotation_label")).scalar() == 1
