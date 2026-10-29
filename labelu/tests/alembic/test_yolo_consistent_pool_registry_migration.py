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
        / "d2e3f4a5b6c7_add_yolo_consistent_pool_registry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "consistent_pool_registry_migration", path
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


def test_migration_backfills_one_registry_per_signature_and_is_reentrant(monkeypatch):
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "CREATE TABLE task ("
                "id INTEGER PRIMARY KEY, "
                "source_type VARCHAR(64), "
                "source_external_task_id VARCHAR(128), "
                "config TEXT, "
                "deleted_at DATETIME)"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO task "
                "(id, source_type, source_external_task_id, config, deleted_at) "
                "VALUES "
                "(11, 'consistent_pool', 'same-signature', '{}', NULL), "
                "(12, 'consistent_pool', 'same-signature', '{}', NULL), "
                "(13, NULL, NULL, :config, NULL), "
                "(14, 'consistent_pool', 'deleted-signature', '{}', CURRENT_TIMESTAMP)"
            ),
            {"config": _pool_config("config-signature")},
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        migration.upgrade()

        rows = bind.execute(
            sa.text(
                "SELECT label_set_signature, task_id "
                "FROM yolo_consistent_pool_registry "
                "ORDER BY task_id"
            )
        ).all()
        assert rows == [("same-signature", 11), ("config-signature", 13)]

        with pytest.raises(sa.exc.IntegrityError):
            with bind.begin_nested():
                bind.execute(
                    sa.text(
                        "INSERT INTO yolo_consistent_pool_registry "
                        "(label_set_signature, task_id) "
                        "VALUES ('same-signature', NULL)"
                    )
                )

        with pytest.raises(sa.exc.IntegrityError):
            with bind.begin_nested():
                bind.execute(
                    sa.text(
                        "INSERT INTO yolo_consistent_pool_registry "
                        "(label_set_signature, task_id) "
                        "VALUES ('another-signature', 11)"
                    )
                )


def test_migration_revision_follows_current_experiment_group_head():
    migration = _load_migration_module()

    assert migration.revision == "d2e3f4a5b6c7"
    assert migration.down_revision == "c1d2e3f4a5b6"
