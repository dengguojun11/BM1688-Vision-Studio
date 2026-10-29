from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "o3p4q5r6s7t8_scope_consistent_pools_by_dataset_category.py"
    )
    spec = importlib.util.spec_from_file_location(
        "consistent_pool_dataset_category_scope_migration", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pool_config(signature: str) -> str:
    return json.dumps(
        {
            "consistent_pool": {
                "kind": "train_filtered_consistent",
                "label_set_signature": signature,
            }
        }
    )


def _pool_sample(source_task_id: int, source_sample_id: int) -> str:
    return json.dumps(
        {
            "_consistent_pool": {
                "source_task_id": source_task_id,
                "source_sample_id": source_sample_id,
            }
        }
    )


def test_migration_moves_legacy_pools_back_to_source_categories(monkeypatch):
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "CREATE TABLE dataset_category ("
                "id INTEGER PRIMARY KEY, category_key VARCHAR(128), name VARCHAR(128), "
                "status VARCHAR(32), deleted_at DATETIME, updated_at DATETIME)"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO dataset_category "
                "(id, category_key, name, status) VALUES "
                "(1, 'garbage', '垃圾暴露', 'active'), "
                "(2, 'parking', '非机动车违停', 'active'), "
                "(13, 'training_consistent_pool', '训练一致数据集', 'active')"
            )
        )
        bind.execute(
            sa.text(
                "CREATE TABLE task ("
                "id INTEGER PRIMARY KEY, source_type VARCHAR(64), "
                "source_external_task_id VARCHAR(128), config TEXT, "
                "dataset_category_id INTEGER, deleted_at DATETIME)"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO task "
                "(id, source_type, source_external_task_id, config, "
                "dataset_category_id, deleted_at) VALUES "
                "(101, 'internal', NULL, '{}', 1, NULL), "
                "(102, 'internal', NULL, '{}', 2, NULL), "
                "(103, 'internal', NULL, '{}', NULL, NULL), "
                "(220, 'consistent_pool', 'same-signature', :pool, 13, NULL), "
                "(221, 'consistent_pool', 'same-signature', :pool, 13, NULL), "
                "(222, 'consistent_pool', 'mixed-signature', :mixed_pool, 13, NULL)"
            ),
            {
                "pool": _pool_config("same-signature"),
                "mixed_pool": _pool_config("mixed-signature"),
            },
        )
        bind.execute(
            sa.text(
                "CREATE TABLE task_sample ("
                "id INTEGER PRIMARY KEY, task_id INTEGER, data TEXT, deleted_at DATETIME)"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO task_sample (id, task_id, data, deleted_at) VALUES "
                "(1, 220, :first, NULL), (2, 221, :second, NULL), "
                "(3, 222, :mixed_first, NULL), (4, 222, :mixed_second, NULL)"
            ),
            {
                "first": _pool_sample(101, 1001),
                "second": _pool_sample(102, 1002),
                "mixed_first": _pool_sample(101, 1003),
                "mixed_second": _pool_sample(103, 1004),
            },
        )
        bind.execute(
            sa.text(
                "CREATE TABLE yolo_consistent_pool_registry ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "label_set_signature VARCHAR(64) NOT NULL, task_id INTEGER, "
                "created_at DATETIME, updated_at DATETIME, "
                "CONSTRAINT uq_yolo_consistent_pool_registry_signature "
                "UNIQUE (label_set_signature), "
                "CONSTRAINT uq_yolo_consistent_pool_registry_task UNIQUE (task_id))"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO yolo_consistent_pool_registry "
                "(label_set_signature, task_id) VALUES ('same-signature', 220)"
            )
        )

        operations = Operations(
            MigrationContext.configure(bind, opts={"render_as_batch": True})
        )
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()

        task_rows = bind.execute(
            sa.text(
                "SELECT id, dataset_category_id, config FROM task "
                "WHERE id IN (220, 221, 222) ORDER BY id"
            )
        ).all()
        assert [(row[0], row[1]) for row in task_rows] == [
            (220, 1),
            (221, 2),
            (222, None),
        ]
        assert (
            json.loads(task_rows[0][2])["consistent_pool"]["dataset_category_key"]
            == "garbage"
        )
        assert (
            json.loads(task_rows[1][2])["consistent_pool"]["dataset_category_key"]
            == "parking"
        )
        assert (
            json.loads(task_rows[2][2])["consistent_pool"]["dataset_category_scope"]
            == "uncategorized"
        )

        registry_rows = bind.execute(
            sa.text(
                "SELECT category_scope_key, dataset_category_id, "
                "label_set_signature, task_id "
                "FROM yolo_consistent_pool_registry ORDER BY task_id"
            )
        ).all()
        assert registry_rows == [
            ("category:1", 1, "same-signature", 220),
            ("category:2", 2, "same-signature", 221),
            ("uncategorized", None, "mixed-signature", 222),
        ]
        system_category = bind.execute(
            sa.text("SELECT status, deleted_at FROM dataset_category WHERE id = 13")
        ).one()
        assert system_category[0] == "disabled"
        assert system_category[1] is not None

        with pytest.raises(sa.exc.IntegrityError):
            with bind.begin_nested():
                bind.execute(
                    sa.text(
                        "INSERT INTO yolo_consistent_pool_registry "
                        "(category_scope_key, dataset_category_id, "
                        "label_set_signature, task_id) "
                        "VALUES ('category:1', 1, 'same-signature', NULL)"
                    )
                )


def test_migration_follows_existing_contract_head():
    migration = _load_migration_module()

    assert migration.revision == "o3p4q5r6s7t8"
    assert migration.down_revision == "h6i7j8k9l0m1"
