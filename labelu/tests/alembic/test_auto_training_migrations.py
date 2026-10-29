from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


VERSIONS = Path(__file__).resolve().parents[2] / "alembic_labelu" / "versions"
MIGRATIONS = [
    "i7j8k9l0m1n2_add_auto_training_core_tables.py",
    "j8k9l0m1n2o3_add_auto_training_governance_tables.py",
    "k9l0m1n2o3p4_link_auto_training_existing_pipeline.py",
    "l0m1n2o3p4q5_seed_auto_training_technical_switches.py",
    "m1n2o3p4q5r6_add_auto_training_publish_lifecycle.py",
    "n2o3p4q5r6s7_add_auto_training_evaluator_request.py",
]


def _load(filename: str):
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_schema(bind):
    bind.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE task_sample (id INTEGER PRIMARY KEY, updated_at DATETIME)"))
    bind.execute(sa.text("CREATE TABLE dataset_category (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE training_task (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE yolo_train_job (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE yolo_bm1688_export (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE yolo_edge_validation_job (id INTEGER PRIMARY KEY)"))
    bind.execute(sa.text("CREATE TABLE system_config_item (id INTEGER PRIMARY KEY, config_key VARCHAR(128) UNIQUE)"))


def test_migrations_are_additive_reentrant_and_do_not_backfill_history(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        _base_schema(bind)
        bind.execute(sa.text("INSERT INTO task_sample (id) VALUES (1), (2), (3)"))
        operations = Operations(MigrationContext.configure(bind))
        for filename in MIGRATIONS:
            module = _load(filename)
            monkeypatch.setattr(module, "op", operations)
            module.upgrade()
            module.upgrade()

        inspector = sa.inspect(bind)
        expected = {
            "auto_training_plan",
            "auto_training_plan_version",
            "auto_training_task_scope",
            "auto_training_sample_pool",
            "sample_human_review",
            "auto_training_round",
            "auto_training_round_sample",
            "auto_training_outbox",
            "auto_training_evaluation_run",
            "auto_training_audit_event",
        }
        assert expected <= set(inspector.get_table_names())
        assert bind.execute(sa.text("SELECT COUNT(*) FROM auto_training_sample_pool")).scalar() == 0
        assert bind.execute(sa.text("SELECT COUNT(*) FROM auto_training_round")).scalar() == 0

        round_columns = {
            column["name"] for column in inspector.get_columns("auto_training_round")
        }
        assert {
            "publish_job_id",
            "publish_status",
            "published_model_id",
            "publish_requested_by",
            "publish_reason",
            "publish_error",
            "publish_requested_at",
            "published_at",
        } <= round_columns

        linked = {
            column["name"] for column in inspector.get_columns("yolo_train_job")
        }
        assert {
            "auto_training_plan_id",
            "auto_training_round_id",
            "queue_priority",
            "queue_source",
            "execution_signature",
        } <= linked

        evaluation_columns = {
            column["name"]
            for column in inspector.get_columns("auto_training_evaluation_run")
        }
        assert {
            "request_manifest_rel_path",
            "request_manifest_sha256",
            "evaluator_worker_id",
            "evaluator_claimed_at",
            "evaluator_lease_expires_at",
        } <= evaluation_columns


def test_migration_chain_follows_current_head():
    modules = [_load(name) for name in MIGRATIONS]

    assert modules[0].down_revision == "o3p4q5r6s7t8"
    assert [item.down_revision for item in modules[1:]] == [
        modules[0].revision,
        modules[1].revision,
        modules[2].revision,
        modules[3].revision,
        modules[4].revision,
    ]
