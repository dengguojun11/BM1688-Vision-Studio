from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "z6b7c8d9e0f1_remove_provider_project_scope.py"
    )
    spec = importlib.util.spec_from_file_location("provider_no_project", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_schema(bind):
    bind.exec_driver_sql("CREATE TABLE project (id INTEGER PRIMARY KEY)")
    bind.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
    bind.exec_driver_sql(
        "CREATE TABLE provider_job (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
        "provider_key VARCHAR(128) NOT NULL, provider_version VARCHAR(64) NOT NULL, "
        "request_signature VARCHAR(128) NOT NULL, status VARCHAR(24) NOT NULL, "
        "created_by INTEGER NOT NULL, updated_at DATETIME, "
        "CONSTRAINT uq_provider_job_project_request UNIQUE(project_id, request_signature))"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_provider_job_project_status ON provider_job(project_id, status, updated_at)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE prediction_set (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
        "provider_job_id INTEGER NOT NULL, version INTEGER NOT NULL, review_status VARCHAR(24) NOT NULL, "
        "created_by INTEGER NOT NULL, created_at DATETIME)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_prediction_set_project_review ON prediction_set(project_id, review_status, created_at)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE prediction_set_review_audit (id INTEGER PRIMARY KEY, prediction_set_id INTEGER NOT NULL, "
        "project_id INTEGER NOT NULL, from_status VARCHAR(24) NOT NULL, to_status VARCHAR(24) NOT NULL, "
        "actor_id INTEGER NOT NULL, idempotency_key VARCHAR(128) NOT NULL, created_at DATETIME)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_prediction_set_review_audit_project_time ON prediction_set_review_audit(project_id, created_at)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE prediction_set_projection (id INTEGER PRIMARY KEY, prediction_set_id INTEGER NOT NULL, "
        "project_id INTEGER NOT NULL, provider_job_id INTEGER NOT NULL, task_id INTEGER NOT NULL, "
        "sample_id INTEGER NOT NULL, pre_annotation_id INTEGER NOT NULL, idempotency_key VARCHAR(128) NOT NULL, "
        "created_by INTEGER NOT NULL, created_at DATETIME, "
        "CONSTRAINT uq_prediction_set_projection_set UNIQUE(prediction_set_id), "
        "CONSTRAINT uq_prediction_set_projection_request UNIQUE(idempotency_key))"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_prediction_set_projection_project_time ON prediction_set_projection(project_id, created_at)"
    )


def test_provider_project_scope_migration_is_idempotent_and_nullable():
    module = _load()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        _create_legacy_schema(bind)
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        module.upgrade()
        module.upgrade()
        inspector = sa.inspect(bind)
        job_project = next(c for c in inspector.get_columns("provider_job") if c["name"] == "project_id")
        assert job_project["nullable"] is True
        for table in ("prediction_set", "prediction_set_review_audit", "prediction_set_projection"):
            column = next(c for c in inspector.get_columns(table) if c["name"] == "project_id")
            assert column["nullable"] is True
        assert "uq_provider_job_creator_request" in {
            item.get("name") for item in inspector.get_unique_constraints("provider_job")
        }
        assert "idx_provider_job_creator_status" in {
            item.get("name") for item in inspector.get_indexes("provider_job")
        }
        assert "idx_prediction_set_creator_review" in {
            item.get("name") for item in inspector.get_indexes("prediction_set")
        }


def test_provider_project_scope_migration_rejects_creator_signature_collision():
    module = _load()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        _create_legacy_schema(bind)
        bind.exec_driver_sql(
            "INSERT INTO provider_job(id, project_id, provider_key, provider_version, request_signature, status, created_by) "
            "VALUES (1, 1, 'yolo', '1', 'same', 'QUEUED', 9), (2, 2, 'yolo', '1', 'same', 'QUEUED', 9)"
        )
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        try:
            module.upgrade()
        except RuntimeError as exc:
            assert "重复数据" in str(exc)
        else:
            raise AssertionError("migration must reject creator/request collisions")
