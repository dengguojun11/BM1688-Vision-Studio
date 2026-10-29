from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "e3f4a5b6c7d8_add_sample_annotation_label_index.py"
    )
    spec = importlib.util.spec_from_file_location("sample_label_index_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data(*labels: str) -> str:
    return json.dumps(
        {
            "result": json.dumps(
                {
                    "rectTool": {
                        "result": [
                            {"id": str(index), "label": label}
                            for index, label in enumerate(labels)
                        ]
                    }
                }
            )
        }
    )


def test_migration_backfills_distinct_rect_labels_and_is_reentrant(monkeypatch):
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY)"))
        bind.execute(
            sa.text(
                "CREATE TABLE task_sample ("
                "id INTEGER PRIMARY KEY, task_id INTEGER, data TEXT, deleted_at DATETIME)"
            )
        )
        bind.execute(sa.text("INSERT INTO task (id) VALUES (10), (11)"))
        bind.execute(
            sa.text(
                "INSERT INTO task_sample (id, task_id, data, deleted_at) VALUES "
                "(1, 10, :first, NULL), "
                "(2, 10, :second, NULL), "
                "(3, 11, :deleted, CURRENT_TIMESTAMP)"
            ),
            {
                "first": _data("vehicle", "vehicle", "person"),
                "second": _data("person"),
                "deleted": _data("ignored"),
            },
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        migration.upgrade()

        rows = bind.execute(
            sa.text(
                "SELECT sample_id, task_id, label_value "
                "FROM sample_annotation_label_index "
                "ORDER BY sample_id, label_value"
            )
        ).all()
        assert rows == [
            (1, 10, "person"),
            (1, 10, "vehicle"),
            (2, 10, "person"),
        ]


def test_migration_revision_follows_consistent_pool_registry_head():
    migration = _load_migration_module()

    assert migration.revision == "e3f4a5b6c7d8"
    assert migration.down_revision == "d2e3f4a5b6c7"
