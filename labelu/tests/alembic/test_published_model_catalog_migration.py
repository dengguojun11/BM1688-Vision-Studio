from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def _load_migration(filename: str, module_name: str):
    path = (
        Path(__file__).resolve().parents[2] / "alembic_labelu" / "versions" / filename
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_published_model_catalog_migration_creates_catalog_and_aliases(monkeypatch):
    migration = _load_migration(
        "e4f5a6b7c8d9_add_published_model_catalog.py",
        "published_model_catalog_migration",
    )
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE yolo_train_job (id INTEGER PRIMARY KEY)"))
        bind.execute(
            sa.text("CREATE TABLE yolo_bm1688_export (id INTEGER PRIMARY KEY)")
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        inspector = sa.inspect(bind)
        assert {
            "published_model_catalog",
            "published_model_identifier_alias",
        } <= set(inspector.get_table_names())
        catalog_columns = {
            column["name"]
            for column in inspector.get_columns("published_model_catalog")
        }
        assert {
            "public_model_id",
            "aicm_model_set_id",
            "export_id",
            "status",
            "is_public",
            "availability_status",
            "remote_download_url",
        } <= catalog_columns
        alias_uniques = inspector.get_unique_constraints(
            "published_model_identifier_alias"
        )
        assert any(
            set(item["column_names"]) == {"source_system", "external_model_id"}
            for item in alias_uniques
        )


def test_published_model_download_switch_migration_upgrades_existing_catalog(
    monkeypatch,
):
    catalog_migration = _load_migration(
        "e4f5a6b7c8d9_add_published_model_catalog.py",
        "published_model_catalog_migration_for_switch",
    )
    switch_migration = _load_migration(
        "f5a6b7c8d9e0_add_published_model_download_switch.py",
        "published_model_download_switch_migration",
    )
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE yolo_train_job (id INTEGER PRIMARY KEY)"))
        bind.execute(
            sa.text("CREATE TABLE yolo_bm1688_export (id INTEGER PRIMARY KEY)")
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(catalog_migration, "op", operations)
        monkeypatch.setattr(switch_migration, "op", operations)
        catalog_migration.upgrade()
        switch_migration.upgrade()

        columns = {
            column["name"]: column
            for column in sa.inspect(bind).get_columns("published_model_catalog")
        }
        assert columns["download_enabled"]["nullable"] is False
        bind.execute(
            sa.text(
                "INSERT INTO published_model_catalog "
                "(public_model_id, name, display_name, source_type, source_system, "
                "status, availability_status, created_at, updated_at) "
                "VALUES ('701', '', '', 'remote_legacy', 'aicm', 'active', "
                "'available', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        assert bind.execute(
            sa.text(
                "SELECT download_enabled FROM published_model_catalog "
                "WHERE public_model_id='701'"
            )
        ).scalar_one() in (1, True)


def test_published_model_download_switch_migration_follows_catalog_head():
    catalog_migration = _load_migration(
        "e4f5a6b7c8d9_add_published_model_catalog.py",
        "published_model_catalog_head_migration",
    )
    switch_migration = _load_migration(
        "f5a6b7c8d9e0_add_published_model_download_switch.py",
        "published_model_download_switch_head_migration",
    )

    assert catalog_migration.revision == "e4f5a6b7c8d9"
    assert catalog_migration.down_revision == "d3e4f5a6b7c8"
    assert switch_migration.revision == "f5a6b7c8d9e0"
    assert switch_migration.down_revision == catalog_migration.revision


def test_published_model_package_path_migration_follows_download_switch(monkeypatch):
    catalog_migration = _load_migration(
        "e4f5a6b7c8d9_add_published_model_catalog.py",
        "published_model_catalog_migration_for_package_path",
    )
    switch_migration = _load_migration(
        "f5a6b7c8d9e0_add_published_model_download_switch.py",
        "published_model_download_switch_for_package_path",
    )
    path_migration = _load_migration(
        "g6h7i8j9k0l1_add_published_model_package_path.py",
        "published_model_package_path_migration",
    )
    precision_migration = _load_migration(
        "h7i8j9k0l1m2_add_published_model_precision.py",
        "published_model_precision_migration",
    )
    engine = sa.create_engine("sqlite:///:memory:")

    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE task (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE yolo_train_job (id INTEGER PRIMARY KEY)"))
        bind.execute(
            sa.text("CREATE TABLE yolo_bm1688_export (id INTEGER PRIMARY KEY)")
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(catalog_migration, "op", operations)
        monkeypatch.setattr(switch_migration, "op", operations)
        monkeypatch.setattr(path_migration, "op", operations)
        monkeypatch.setattr(precision_migration, "op", operations)
        catalog_migration.upgrade()
        switch_migration.upgrade()
        path_migration.upgrade()
        precision_migration.upgrade()

        columns = {
            column["name"]: column
            for column in sa.inspect(bind).get_columns("published_model_catalog")
        }
        assert columns["package_rel_path"]["nullable"] is True
        assert columns["precision"]["nullable"] is True

    assert path_migration.down_revision == switch_migration.revision
    assert precision_migration.down_revision == path_migration.revision
