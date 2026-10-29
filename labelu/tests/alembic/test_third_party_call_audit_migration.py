from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def _load_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "r5s6t7u8v9w0_add_third_party_call_audit.py"
    )
    spec = importlib.util.spec_from_file_location("third_party_call_audit_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_merge_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "s5t6u7v8w9x0_merge_auto_training_and_third_party_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "third_party_call_audit_merge_migration", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_third_party_call_audit_migration_upgrade_and_downgrade(monkeypatch):
    migration = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        inspector = sa.inspect(bind)
        assert "third_party_call_audit" in inspector.get_table_names()
        columns = {
            column["name"]
            for column in inspector.get_columns("third_party_call_audit")
        }
        assert {
            "request_id",
            "source_system",
            "operation",
            "external_task_id",
            "external_package_id",
            "response_status",
            "duration_ms",
            "error_summary",
        } <= columns

        migration.downgrade()
        assert "third_party_call_audit" not in sa.inspect(bind).get_table_names()


def test_third_party_call_audit_merges_current_local_heads():
    migration = _load_migration()
    merge = _load_merge_migration()

    assert migration.down_revision == "p4q5r6s7t8u9"
    assert merge.down_revision == (
        "q5r6s7t8u9v0",
        migration.revision,
    )
