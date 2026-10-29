from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_media_upload_security_migration_is_idempotent():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "a0b1c2d3e4f5_media_scan_fields.py"
    )
    spec = importlib.util.spec_from_file_location("media_upload_security", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "CREATE TABLE media_upload_session ("
                "id INTEGER PRIMARY KEY, status VARCHAR(24) NOT NULL, "
                "updated_at DATETIME)"
            )
        )
        operations = Operations(MigrationContext.configure(bind))
        module.op = operations
        module.upgrade()
        module.upgrade()

        columns = {
            item["name"] for item in sa.inspect(bind).get_columns("media_upload_session")
        }
        indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes("media_upload_session")
        }
        assert {
            "scan_status",
            "scan_provider",
            "scan_signature",
            "scan_error",
            "scanned_at",
            "quarantined_at",
            "quarantine_path",
            "quarantine_until",
        } <= columns
        assert "idx_media_upload_scan_status" in indexes
        assert "ix_media_upload_session_quarantine_until" in indexes
        assert bind.execute(
            sa.text("SELECT scan_status FROM media_upload_session")
        ).fetchall() == []
