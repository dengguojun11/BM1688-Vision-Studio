"""Formal execution-lease migration contract and isolated SQLite rehearsal."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)

ROOT = Path(__file__).parents[2]
MIGRATION = (
    ROOT
    / "alembic_labelu"
    / "versions"
    / "z4a5b6c7d8e9_add_execution_leases_and_bm1688_worker_actions.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "formal_execution_lease_migration", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module.op = Operations(MigrationContext.configure(connection))
    operation()


def _create_tables(engine):
    metadata = MetaData()
    Table(
        "yolo_train_job",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
    )
    Table(
        "yolo_bm1688_export",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
    )
    Table(
        "yolo_edge_validation_job",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
        Column("queued_at", DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(engine)


def test_formal_migration_is_single_child_of_current_head():
    module = _load_migration()
    assert module.revision == "z4a5b6c7d8e9"
    assert module.down_revision == "z3a4b5c6d7e8"


def test_formal_migration_is_idempotent_and_backup_restore_only(tmp_path):
    module = _load_migration()
    engine = create_engine(f"sqlite:///{(tmp_path / 'leases.sqlite').as_posix()}")
    try:
        _create_tables(engine)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO yolo_bm1688_export (status) VALUES ('PENDING')")
            )
            _run(module, connection, module.upgrade)
            _run(module, connection, module.upgrade)

        inspector = inspect(engine)
        for table_name in module._TABLES:
            columns = {item["name"] for item in inspector.get_columns(table_name)}
            indexes = {item.get("name") for item in inspector.get_indexes(table_name)}
            assert set(module._TABLES[table_name]["columns"]) <= columns
            assert set(module._TABLES[table_name]["indexes"]) <= indexes
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT worker_action FROM yolo_bm1688_export")
                ).scalar_one()
                is None
            )

        with engine.begin() as connection:
            try:
                _run(module, connection, module.downgrade)
            except RuntimeError as exc:
                assert "备份" in str(exc)
            else:
                raise AssertionError("正式执行租约迁移不得执行危险自动降级")
    finally:
        engine.dispose()
