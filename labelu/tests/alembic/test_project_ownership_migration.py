from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_project_ownership_migration_backfills_deterministically():
    path = Path(__file__).resolve().parents[2] / "alembic_labelu" / "versions" / "v5w6x7y8z9a0_add_project_ownership_to_tasks_and_training_jobs.py"
    spec = importlib.util.spec_from_file_location("project_ownership", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE project (id INTEGER PRIMARY KEY, owner_id INTEGER, deleted_at DATETIME)"))
        bind.execute(sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY, created_by INTEGER)"))
        bind.execute(sa.text("CREATE TABLE yolo_train_job (id INTEGER PRIMARY KEY, task_id INTEGER)"))
        bind.execute(sa.text("INSERT INTO project VALUES (2, 7, NULL), (1, 7, NULL)"))
        bind.execute(sa.text("INSERT INTO task VALUES (10, 7)"))
        bind.execute(sa.text("INSERT INTO yolo_train_job VALUES (20, 10)"))
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        module.upgrade()
        module.upgrade()
        assert bind.execute(sa.text("SELECT project_id FROM task WHERE id=10")).scalar() == 1
        assert bind.execute(sa.text("SELECT project_id FROM yolo_train_job WHERE id=20")).scalar() == 1
