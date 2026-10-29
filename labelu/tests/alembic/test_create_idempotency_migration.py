"""Isolated rehearsal for creator-scoped create idempotency columns."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic_labelu" / "versions" / "za0b1c2d3e4f_add_create_idempotency.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("create_idempotency_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module.op = Operations(MigrationContext.configure(connection))
    operation()


def test_create_idempotency_migration_is_repeatable_and_unique(tmp_path):
    module = _load_migration()
    engine = create_engine(f"sqlite:///{(tmp_path / 'idempotency.sqlite').as_posix()}")
    metadata = MetaData()
    for table_name in ("task", "yolo_train_job"):
        Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("created_by", Integer, nullable=False),
        )
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _run(module, connection, module.upgrade)
            _run(module, connection, module.upgrade)
            for table_name, constraint_name in module._TABLES.items():
                columns = {item["name"] for item in inspect(connection).get_columns(table_name)}
                assert {"idempotency_key", "idempotency_request_hash"} <= columns
                constraints = {
                    item.get("name") for item in inspect(connection).get_unique_constraints(table_name)
                }
                assert constraint_name in constraints
            connection.execute(
                text(
                    "INSERT INTO task (created_by, idempotency_key) "
                    "VALUES (1, 'retry-key')"
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO task (created_by, idempotency_key) "
                        "VALUES (1, 'retry-key')"
                    )
                )
    finally:
        engine.dispose()
