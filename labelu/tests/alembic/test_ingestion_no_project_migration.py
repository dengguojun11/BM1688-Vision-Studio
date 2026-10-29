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
        / "z5a6b7c8d9e0_remove_ingestion_project_scope.py"
    )
    spec = importlib.util.spec_from_file_location("ingestion_no_project", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_schema(bind):
    bind.exec_driver_sql(
        "CREATE TABLE project (id INTEGER PRIMARY KEY, name VARCHAR(128))"
    )
    bind.exec_driver_sql(
        "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(128))"
    )
    bind.exec_driver_sql(
        "CREATE TABLE ingestion_job ("
        "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, created_by INTEGER NOT NULL, "
        "idempotency_key VARCHAR(256) NOT NULL, status VARCHAR(24) NOT NULL, updated_at DATETIME, "
        "CONSTRAINT uq_ingestion_job_project_idempotency UNIQUE(project_id, idempotency_key))"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_ingestion_job_project_status ON ingestion_job(project_id, status, updated_at)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE media_asset ("
        "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, created_by INTEGER NOT NULL, "
        "sha256 VARCHAR(64) NOT NULL, media_type VARCHAR(32) NOT NULL, created_at DATETIME, "
        "scan_status VARCHAR(24) NOT NULL, lifecycle_status VARCHAR(24) NOT NULL, "
        "CONSTRAINT uq_media_asset_project_sha_media UNIQUE(project_id, sha256, media_type))"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_media_asset_project_scan ON media_asset(project_id, scan_status, lifecycle_status)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX idx_media_asset_scene ON media_asset(project_id, created_at)"
    )


def test_ingestion_project_scope_migration_is_idempotent_and_nullable():
    module = _load()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        _create_legacy_schema(bind)
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        module.upgrade()
        module.upgrade()

        inspector = sa.inspect(bind)
        for table in ("ingestion_job", "media_asset"):
            project = next(c for c in inspector.get_columns(table) if c["name"] == "project_id")
            assert project["nullable"] is True
        job_unique = {item.get("name") for item in inspector.get_unique_constraints("ingestion_job")}
        asset_unique = {item.get("name") for item in inspector.get_unique_constraints("media_asset")}
        assert "uq_ingestion_job_creator_idempotency" in job_unique
        assert "uq_media_asset_creator_sha_media" in asset_unique
        assert "idx_ingestion_job_creator_status" in {
            item.get("name") for item in inspector.get_indexes("ingestion_job")
        }
        assert "idx_media_asset_creator_scan" in {
            item.get("name") for item in inspector.get_indexes("media_asset")
        }


def test_ingestion_project_scope_migration_fails_closed_on_creator_collision():
    module = _load()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        _create_legacy_schema(bind)
        bind.exec_driver_sql(
            "INSERT INTO ingestion_job(id, project_id, created_by, idempotency_key, status) "
            "VALUES (1, 1, 9, 'same', 'QUEUED'), (2, 2, 9, 'same', 'QUEUED')"
        )
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        try:
            module.upgrade()
        except RuntimeError as exc:
            assert "重复数据" in str(exc)
        else:
            raise AssertionError("migration must reject creator/idempotency collisions")
