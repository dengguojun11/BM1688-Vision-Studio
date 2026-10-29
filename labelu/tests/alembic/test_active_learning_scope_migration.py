"""Active-learning global scope migration contract and SQLite rehearsal."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, UniqueConstraint, create_engine, inspect, text


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic_labelu" / "versions" / "z9d1e2f3a4b5_add_active_learning_global_scope.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("active_learning_scope_migration", MIGRATION)
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
        "feedback_cluster",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, nullable=False),
        Column("cluster_key", String(128), nullable=False),
        Column("status", String(24), nullable=False),
        Column("updated_at", DateTime, nullable=True),
        UniqueConstraint(
            "project_id", "cluster_key", name="uq_feedback_cluster_project_key"
        ),
    )
    Table(
        "active_learning_batch",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, nullable=False),
        Column("batch_key", String(160), nullable=False),
        Column("status", String(24), nullable=False),
        Column("updated_at", DateTime, nullable=True),
        UniqueConstraint(
            "project_id", "batch_key", name="uq_active_learning_batch_key"
        ),
    )
    metadata.create_all(engine)


def test_active_learning_migration_chain_and_global_backfill(tmp_path):
    module = _load_migration()
    assert module.revision == "z9d1e2f3a4b5"
    assert module.down_revision == "z9c0d1e2f3a4"

    engine = create_engine(f"sqlite:///{(tmp_path / 'active-learning.sqlite').as_posix()}")
    try:
        _create_tables(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO feedback_cluster (id, project_id, cluster_key, status) "
                    "VALUES (1, 17, 'LOW_CONFIDENCE|bm1688', 'READY')"
                )
            )
            _run(module, connection, module.upgrade)
            connection.execute(
                text(
                    "INSERT INTO active_learning_batch (id, project_id, scope_key, batch_key, status) "
                    "VALUES (1, NULL, 'global', 'global-batch', 'READY')"
                )
            )
            _run(module, connection, module.upgrade)

        inspector = inspect(engine)
        for table_name in ("feedback_cluster", "active_learning_batch"):
            columns = {item["name"]: item for item in inspector.get_columns(table_name)}
            assert columns["scope_key"]["nullable"] is False
            assert columns["project_id"]["nullable"] is True
            uniques = {item.get("name") for item in inspector.get_unique_constraints(table_name)}
            expected = "uq_feedback_cluster_scope_key" if table_name == "feedback_cluster" else "uq_active_learning_batch_scope_key"
            assert expected in uniques

        with engine.connect() as connection:
            assert connection.execute(text("SELECT scope_key FROM feedback_cluster WHERE id=1")).scalar_one() == "project:17"
            assert connection.execute(text("SELECT scope_key FROM active_learning_batch WHERE id=1")).scalar_one() == "global"

        with engine.begin() as connection:
            try:
                _run(module, connection, module.downgrade)
            except RuntimeError as exc:
                assert "备份" in str(exc)
            else:
                raise AssertionError("正式主动学习迁移不得自动降级")
    finally:
        engine.dispose()


def test_follow_up_scope_migrations_are_linear():
    root = ROOT / "alembic_labelu" / "versions"
    workflow = (root / "z9e2f3a4b5c6_add_global_workflow_scope.py").read_text(encoding="utf-8")
    dataset = (root / "z9f3a4b5c6d7_make_dataset_project_compat_nullable.py").read_text(encoding="utf-8")
    assert 'down_revision = "z9d1e2f3a4b5"' in workflow
    assert 'down_revision = "z9e2f3a4b5c6"' in dataset
