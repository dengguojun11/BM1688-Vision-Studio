from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_prediction_review_projection_migration_is_reentrant():
    versions_dir = Path(__file__).resolve().parents[2] / "alembic_labelu" / "versions"
    candidates = sorted(versions_dir.glob("*_add_prediction_review_projection.py"))
    assert len(candidates) == 1
    path = candidates[0]
    spec = importlib.util.spec_from_file_location("prediction_review_projection", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        # Referenced tables are enough for SQLite DDL; migration itself owns
        # only the PredictionSet columns and the two new audit tables.
        for table in ("user", "project", "provider_job", "task", "task_sample", "task_pre_annotation"):
            bind.execute(sa.text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
        bind.execute(sa.text("CREATE TABLE prediction_set (id INTEGER PRIMARY KEY, project_id INTEGER, provider_job_id INTEGER, sample_id INTEGER, version INTEGER, annotation_contract_version VARCHAR(64), prediction_schema_version VARCHAR(64), payload_uri VARCHAR(1024), payload_sha256 VARCHAR(64), result_manifest_uri VARCHAR(1024), result_sha256 VARCHAR(64), payload_json TEXT, status VARCHAR(24), review_status VARCHAR(24), reviewed_by INTEGER, reviewed_at DATETIME, review_reason TEXT, source VARCHAR(16), item_count INTEGER, metrics_json TEXT, error_code VARCHAR(64), error_message TEXT, created_by INTEGER, created_at DATETIME, updated_at DATETIME)"))
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        module.upgrade()
        module.upgrade()
        inspector = sa.inspect(bind)
        assert {"prediction_set_review_audit", "prediction_set_projection"} <= set(inspector.get_table_names())
        assert "idx_prediction_set_review_audit_set_time" in {
            item["name"] for item in inspector.get_indexes("prediction_set_review_audit")
        }
        module.downgrade()
        assert "prediction_set_review_audit" not in sa.inspect(bind).get_table_names()
        assert "prediction_set_projection" not in sa.inspect(bind).get_table_names()
