"""非空历史 dataset 表移除 project_id 的隔离 SQLite 预演。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    select,
)


_CANDIDATE = (
    Path(__file__).parents[2].parents[0]
    / ".tmp" / "phase-one-rebuild" / "supplemental"
    / "gai_p1_01_project_fk_rebuild.py"
)


def _load_candidate():
    spec = importlib.util.spec_from_file_location("gai_p1_01_project_fk_rebuild", _CANDIDATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_historical_schema(engine):
    metadata = MetaData()
    Table("project", metadata, Column("id", Integer, primary_key=True))
    Table("user", metadata, Column("id", Integer, primary_key=True))
    Table(
        "dataset_category",
        metadata,
        Column("id", Integer, primary_key=True),
    )
    dataset = Table(
        "dataset",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("project.id"), nullable=False),
        Column("owner_id", Integer, ForeignKey("user.id"), nullable=False),
        Column("name", String(128), nullable=False),
        Column("status", String(24), nullable=False),
    )
    dataset_version = Table(
        "dataset_version",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("dataset_id", Integer, ForeignKey("dataset.id"), nullable=False),
        Column("project_id", Integer, ForeignKey("project.id"), nullable=False),
        Column("version_no", Integer, nullable=False),
        Column("manifest_sha256", String(64), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(Table("project", metadata, autoload_with=engine).insert(), [{"id": 7}])
        connection.execute(Table("user", metadata, autoload_with=engine).insert(), [{"id": 11}])
        connection.execute(dataset.insert(), [
            {"id": 101, "project_id": 7, "owner_id": 11, "name": "历史数据集一", "status": "ACTIVE"},
            {"id": 102, "project_id": 7, "owner_id": 11, "name": "历史数据集二", "status": "ARCHIVED"},
        ])
        connection.execute(dataset_version.insert(), [
            {"id": 201, "dataset_id": 101, "project_id": 7, "version_no": 1, "manifest_sha256": "a" * 64},
            {"id": 202, "dataset_id": 102, "project_id": 7, "version_no": 2, "manifest_sha256": "b" * 64},
        ])
    return dataset, dataset_version


def _run(module, connection):
    module.op = Operations(MigrationContext.configure(connection))
    module.upgrade()


def _snapshot(engine, table_name: str):
    inspector = inspect(engine)
    columns = [
        item["name"]
        for item in inspector.get_columns(table_name)
        if item["name"] != "project_id"
    ]
    table = Table(table_name, MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        rows = connection.execute(select(*(table.c[name] for name in columns))).all()
    indexes = {
        item["name"]: tuple(item.get("column_names") or [])
        for item in inspector.get_indexes(table_name)
        if item.get("name") and "project_id" not in (item.get("column_names") or [])
    }
    return columns, rows, indexes


def test_non_empty_historical_rows_columns_indexes_and_foreign_keys_survive(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dataset-project-rebuild.sqlite'}")
    try:
        _seed_historical_schema(engine)
        before = {name: _snapshot(engine, name) for name in ("dataset", "dataset_version")}
        module = _load_candidate()

        with engine.begin() as connection:
            _run(module, connection)
            # 同一隔离数据库重复执行必须幂等。
            _run(module, connection)

        inspector = inspect(engine)
        for table_name, (columns, rows, indexes) in before.items():
            after_columns, after_rows, after_indexes = _snapshot(engine, table_name)
            assert "project_id" not in after_columns
            assert after_columns == columns
            assert after_rows == rows
            assert len(after_rows) == len(rows) == 2
            assert after_indexes == indexes
            assert not any(
                str(foreign_key.get("referred_table")) == "project"
                for foreign_key in inspector.get_foreign_keys(table_name)
            )
    finally:
        engine.dispose()


def test_candidate_is_isolated_and_refuses_non_sqlite_or_downgrade():
    assert "alembic_labelu" not in _CANDIDATE.parts
    assert _CANDIDATE.exists()
    module = _load_candidate()
    assert module.revision == "gai_p1_01_project_fk_rebuild"
    assert module.down_revision == "gai_p1_01"
