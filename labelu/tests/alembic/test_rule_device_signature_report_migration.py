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
    / "p2q3r4s5t6u7_add_rule_device_signature_reports.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("rule_device_report_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_device_report_migration_adds_binding_state_and_evidence(monkeypatch):
    module = _load()
    assert module.down_revision == "o1p2q3r4s5t6"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE project (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        bind.execute(
            sa.text("CREATE TABLE edge_agent (agent_id VARCHAR(128) PRIMARY KEY)")
        )
        bind.execute(
            sa.text("CREATE TABLE rule_version (id INTEGER PRIMARY KEY)")
        )
        bind.execute(
            sa.text(
                "CREATE TABLE rule_binding (id INTEGER PRIMARY KEY, "
                "project_id INTEGER NOT NULL, device_id VARCHAR(128) NOT NULL, "
                "rule_version_id INTEGER NOT NULL)"
            )
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()

        binding_columns = {
            item["name"]: item
            for item in sa.inspect(bind).get_columns("rule_binding")
        }
        assert {
            "device_verification_status",
            "device_report_sequence",
            "last_device_report_id",
            "device_verified_at",
        } <= set(binding_columns)
        assert binding_columns["device_verification_status"]["nullable"] is False
        assert binding_columns["device_report_sequence"]["nullable"] is False

        report_columns = {
            item["name"]
            for item in sa.inspect(bind).get_columns("rule_device_signature_report")
        }
        assert {
            "report_id",
            "sequence",
            "project_id",
            "device_id",
            "rule_binding_id",
            "rule_version_id",
            "payload_sha256",
            "key_id",
            "key_state",
            "reported_status",
            "effective_status",
            "received_at",
        } <= report_columns
        unique_sets = {
            tuple(item["column_names"])
            for item in sa.inspect(bind).get_unique_constraints(
                "rule_device_signature_report"
            )
        }
        assert ("report_id",) in unique_sets
        assert ("rule_binding_id", "sequence") in unique_sets

        module.downgrade()
        assert "rule_device_signature_report" not in sa.inspect(bind).get_table_names()
        remaining = {
            item["name"] for item in sa.inspect(bind).get_columns("rule_binding")
        }
        assert remaining == {"id", "project_id", "device_id", "rule_version_id"}
