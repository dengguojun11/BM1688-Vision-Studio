from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "zj6k7l8m9n0_add_annotation_prompt_binding.py"
    )
    spec = importlib.util.spec_from_file_location("annotation_prompt_binding_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_contract_tables(bind):
    # The migration only owns the prompt table, but its foreign-key targets
    # must exist on a real upgrade path.
    bind.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
    bind.exec_driver_sql("CREATE TABLE dataset (id INTEGER PRIMARY KEY)")
    bind.exec_driver_sql(
        "CREATE TABLE dataset_label_contract (id INTEGER PRIMARY KEY, dataset_id INTEGER)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE task_annotation_contract (id INTEGER PRIMARY KEY, task_id INTEGER)"
    )


def test_annotation_prompt_binding_migration_is_reentrant_and_complete():
    migration = _load_migration()
    assert migration.revision == "zj6k7l8m9n0"
    assert migration.down_revision == "zi5j6k7l8m9"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        _create_contract_tables(bind)
        operations = Operations(MigrationContext.configure(bind))
        migration.op = operations

        migration.upgrade()
        migration.upgrade()

        inspector = sa.inspect(bind)
        columns = {column["name"] for column in inspector.get_columns("annotation_prompt_binding")}
        assert {
            "dataset_id",
            "dataset_label_contract_id",
            "task_annotation_contract_id",
            "effective_prompt",
            "prompt_sha256",
            "status",
        }.issubset(columns)
        indexes = {index["name"] for index in inspector.get_indexes("annotation_prompt_binding")}
        assert "ix_annotation_prompt_binding_dataset_contract" in indexes
        assert "ix_annotation_prompt_binding_task_contract" in indexes
        foreign_keys = {
            tuple(foreign_key["constrained_columns"]): foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("annotation_prompt_binding")
        }
        assert foreign_keys[("dataset_label_contract_id",)] == "dataset_label_contract"
        assert foreign_keys[("task_annotation_contract_id",)] == "task_annotation_contract"

