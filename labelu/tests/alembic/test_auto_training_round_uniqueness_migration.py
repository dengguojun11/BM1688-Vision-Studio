"""Database-level uniqueness contract for automatic-training round stages."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).parents[2]
MIGRATION = (
    ROOT
    / "alembic_labelu"
    / "versions"
    / "zf2a3b4c5d6e_add_auto_training_round_stage_uniqueness.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "auto_training_round_uniqueness_migration", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation):
    module.op = Operations(MigrationContext.configure(connection))
    operation()


def _create_tables(engine):
    metadata = sa.MetaData()
    for table_name in (
        "training_task",
        "yolo_train_job",
        "yolo_bm1688_export",
        "yolo_edge_validation_job",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("auto_training_round_id", sa.Integer, nullable=True),
        )
    metadata.create_all(engine)


def test_migration_is_the_single_child_of_current_head():
    module = _load_migration()
    assert module.revision == "zf2a3b4c5d6e"
    assert module.down_revision == "ze1f2a3b4c5d"
    assert tuple(module._TABLES) == (
        "training_task",
        "yolo_train_job",
        "yolo_bm1688_export",
        "yolo_edge_validation_job",
    )


def test_migration_is_idempotent_and_allows_multiple_null_rounds(tmp_path):
    module = _load_migration()
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'rounds.sqlite').as_posix()}")
    try:
        _create_tables(engine)
        with engine.begin() as connection:
            _run(module, connection, module.upgrade)
            _run(module, connection, module.upgrade)
            for table_name in module._TABLES:
                connection.execute(
                    sa.text(
                        f"INSERT INTO {table_name} "
                        "(auto_training_round_id) VALUES (NULL), (NULL), (7)"
                    )
                )
                with pytest.raises(sa.exc.IntegrityError):
                    connection.execute(
                        sa.text(
                            f"INSERT INTO {table_name} "
                            "(auto_training_round_id) VALUES (7)"
                        )
                    )

        inspector = sa.inspect(engine)
        for table_name, index_name in module._INDEXES.items():
            indexes = {item["name"]: item for item in inspector.get_indexes(table_name)}
            assert indexes[index_name]["unique"] in (True, 1)
            assert indexes[index_name]["column_names"] == ["auto_training_round_id"]
    finally:
        engine.dispose()


def test_duplicate_round_data_fails_closed_before_any_index_is_created(tmp_path):
    module = _load_migration()
    engine = sa.create_engine(
        f"sqlite:///{(tmp_path / 'duplicates.sqlite').as_posix()}"
    )
    try:
        _create_tables(engine)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO yolo_train_job (auto_training_round_id) "
                    "VALUES (11), (11)"
                )
            )
            with pytest.raises(RuntimeError, match="重复 auto_training_round_id"):
                _run(module, connection, module.upgrade)

        inspector = sa.inspect(engine)
        assert "uq_yolo_train_job_auto_training_round" not in {
            item["name"] for item in inspector.get_indexes("yolo_train_job")
        }
    finally:
        engine.dispose()


def test_downgrade_requires_backup_restore():
    module = _load_migration()
    with pytest.raises(RuntimeError, match="备份"):
        module.downgrade()
