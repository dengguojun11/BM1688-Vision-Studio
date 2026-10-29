from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_scheduler_lease_migration_is_reentrant():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "u4v5w6x7y8z9_add_scheduler_lease.py"
    )
    spec = importlib.util.spec_from_file_location("scheduler_lease_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        module.upgrade()
        module.upgrade()
        inspector = sa.inspect(bind)
        assert "scheduler_lease" in inspector.get_table_names()
        assert {
            "lease_name",
            "lease_owner",
            "fencing_token",
            "lease_expires_at",
            "heartbeat_at",
        } <= {column["name"] for column in inspector.get_columns("scheduler_lease")}
