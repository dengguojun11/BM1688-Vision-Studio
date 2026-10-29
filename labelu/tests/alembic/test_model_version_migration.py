from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic_labelu"
    / "versions"
    / "m2n3o4p5q6r7_add_model_versions_and_quality_gates.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("model_version_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_version_migration_creates_lineage_artifact_and_evidence_tables(
    monkeypatch,
):
    module = _load()
    assert module.down_revision == "k0l1m2n3o4p5"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        # The revision is additive and expects its referenced platform tables
        # to have already been created by earlier revisions.
        for table in (
            "project",
            "algorithm_definition",
            "dataset_version",
            "yolo_train_job",
            "published_model_catalog",
            "user",
        ):
            bind.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()
        inspector = sa.inspect(bind)
        assert {
            "model_version",
            "model_artifact",
            "model_quality_evidence",
        } <= set(inspector.get_table_names())
        model_columns = {
            item["name"] for item in inspector.get_columns("model_version")
        }
        assert {
            "project_id",
            "algorithm_id",
            "dataset_version_id",
            "training_job_id",
            "parent_model_version_id",
            "published_catalog_id",
            "model_key",
            "status",
        } <= model_columns
        evidence_columns = {
            item["name"] for item in inspector.get_columns("model_quality_evidence")
        }
        assert {
            "gate_type",
            "revision",
            "status",
            "evidence_sha256",
        } <= evidence_columns
        module.downgrade()
        assert "model_version" not in sa.inspect(bind).get_table_names()
