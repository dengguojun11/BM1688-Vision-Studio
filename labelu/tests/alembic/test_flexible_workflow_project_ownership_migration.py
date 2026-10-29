from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_flexible_workflow_project_ownership_backfills_known_links():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "y8z9a0b1c2d3_add_project_ownership_to_flexible_workflow.py"
    )
    spec = importlib.util.spec_from_file_location("flexible_workflow_ownership", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(
            sa.text("CREATE TABLE project (id INTEGER PRIMARY KEY)")
        )
        bind.execute(
            sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY, project_id INTEGER)")
        )
        bind.execute(
            sa.text(
                "CREATE TABLE strategy_task_integration ("
                "id INTEGER PRIMARY KEY, labelu_task_id INTEGER, "
                "external_task_id VARCHAR(128), source_package_id VARCHAR(128))"
            )
        )
        bind.execute(
            sa.text(
                "CREATE TABLE third_party_call_audit ("
                "id INTEGER PRIMARY KEY, external_task_id VARCHAR(128), "
                "external_package_id VARCHAR(128))"
            )
        )
        bind.execute(sa.text("INSERT INTO project VALUES (11), (22)"))
        bind.execute(sa.text("INSERT INTO task VALUES (101, 11), (202, 22)"))
        bind.execute(
            sa.text(
                "INSERT INTO strategy_task_integration VALUES "
                "(1, 101, 'task-11', 'pkg-11'), "
                "(2, 202, 'task-22', 'pkg-22'), "
                "(3, NULL, 'task-orphan', 'pkg-orphan')"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO third_party_call_audit VALUES "
                "(1, 'task-11', NULL), "
                "(2, NULL, 'pkg-22'), "
                "(3, 'task-orphan', 'pkg-orphan'), "
                "(4, 'unknown', 'unknown')"
            )
        )

        module.op = Operations(MigrationContext.configure(bind))
        module.upgrade()
        module.upgrade()

        integrations = dict(
            bind.execute(
                sa.text(
                    "SELECT id, project_id FROM strategy_task_integration ORDER BY id"
                )
            ).all()
        )
        audits = dict(
            bind.execute(
                sa.text("SELECT id, project_id FROM third_party_call_audit ORDER BY id")
            ).all()
        )
        assert integrations == {1: 11, 2: 22, 3: None}
        assert audits == {1: 11, 2: 22, 3: None, 4: None}
