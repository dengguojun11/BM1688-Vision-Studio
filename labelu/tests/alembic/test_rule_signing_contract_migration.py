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
    / "o1p2q3r4s5t6_add_rule_signing_contract.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("rule_signing_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_signing_migration_adds_and_removes_contract_columns(monkeypatch):
    module = _load()
    assert module.down_revision == "n0p1q2r3s4t5"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "CREATE TABLE rule_version ("
                "id INTEGER PRIMARY KEY, "
                "status VARCHAR(24) NOT NULL"
                ")"
            )
        )
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()
        columns = {
            item["name"]: item
            for item in sa.inspect(bind).get_columns("rule_version")
        }
        assert {
            "signing_payload_sha256",
            "signature_algorithm",
            "signature_key_id",
            "signature",
            "server_signature_status",
            "device_verification_status",
            "signature_verified_at",
        } <= set(columns)
        assert columns["server_signature_status"]["nullable"] is False
        assert columns["device_verification_status"]["nullable"] is False
        module.downgrade()
        remaining = {
            item["name"] for item in sa.inspect(bind).get_columns("rule_version")
        }
        assert remaining == {"id", "status"}
