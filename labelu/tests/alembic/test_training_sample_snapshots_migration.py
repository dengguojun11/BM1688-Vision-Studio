from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "b0c1d2e3f4a5_expand_training_sample_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("training_snapshot_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_noop_for_sqlite_and_keeps_data(monkeypatch):
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "CREATE TABLE training_task (id INTEGER PRIMARY KEY, "
                "source_sample_snapshot_json TEXT)"
            )
        )
        bind.execute(
            sa.text(
                "CREATE TABLE yolo_train_job (id INTEGER PRIMARY KEY, "
                "source_sample_manifest_json TEXT)"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO training_task (id, source_sample_snapshot_json) "
                "VALUES (1, :value)"
            ),
            {"value": "x" * 100000},
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
        migration.upgrade()
        value = bind.execute(
            sa.text(
                "SELECT source_sample_snapshot_json FROM training_task WHERE id=1"
            )
        ).scalar_one()
    assert value == "x" * 100000


def test_migration_targets_both_snapshot_columns():
    migration = _load_migration_module()
    assert migration.revision == "b0c1d2e3f4a5"
    assert migration._COLUMNS == (
        ("training_task", "source_sample_snapshot_json"),
        ("yolo_train_job", "source_sample_manifest_json"),
    )
