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
    / "k0l1m2n3o4p5_add_algorithm_registry.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("algorithm_registry_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_algorithm_registry_migration_creates_contract_and_seed(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    module = _load()
    assert module.down_revision == "j9k0l1m2n3o4"

    with engine.begin() as bind:
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()

        inspector = sa.inspect(bind)
        assert {"algorithm_group", "algorithm_definition"} <= set(inspector.get_table_names())
        columns = {row["name"] for row in inspector.get_columns("algorithm_definition")}
        assert {
            "algorithm_key",
            "task_type",
            "training_adapter",
            "evaluation_adapter",
            "supported_chips_json",
            "default_recipe",
        } <= columns
        seeded = bind.execute(
            sa.text(
                "SELECT algorithm_key, task_type, training_adapter "
                "FROM algorithm_definition"
            )
        ).mappings().one()
        assert dict(seeded) == {
            "algorithm_key": "yolo_detection",
            "task_type": "object_detection",
            "training_adapter": "yolo_detection",
        }
        assert bind.execute(sa.text("SELECT COUNT(*) FROM algorithm_group")).scalar() == 4

        module.downgrade()
        assert "algorithm_definition" not in sa.inspect(bind).get_table_names()


def test_algorithm_governance_migration_adds_columns_and_backfills_metadata(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    base_module = _load()
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "e6f7a8b9c0d1_add_algorithm_governance_fields.py"
    )
    spec = importlib.util.spec_from_file_location("algorithm_governance_migration", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with engine.begin() as bind:
        operations = Operations(MigrationContext.configure(bind))
        monkeypatch.setattr(base_module, "op", operations)
        base_module.upgrade()
        bind.execute(
            sa.text(
                "UPDATE algorithm_definition SET metadata_json = :metadata "
                "WHERE algorithm_key = 'yolo_detection'"
            ),
            {"metadata": '{"algorithm_kind":"model","runtime_adapter":"runtime-v2","risk_level":"high","supported_formats":["onnx"]}'},
        )
        monkeypatch.setattr(module, "op", operations)
        module.upgrade()
        columns = {row["name"] for row in sa.inspect(bind).get_columns("algorithm_definition")}
        assert {
            "algorithm_kind", "runtime_adapter", "rule_schema_json",
            "supported_formats_json", "min_samples_json", "quality_gate_json",
            "risk_level", "annotation_contract_version",
        } <= columns
        row = bind.execute(
            sa.text(
                "SELECT algorithm_kind, runtime_adapter, supported_formats_json, risk_level "
                "FROM algorithm_definition WHERE algorithm_key='yolo_detection'"
            )
        ).mappings().one()
        assert row["algorithm_kind"] == "model"
        assert row["runtime_adapter"] == "runtime-v2"
        assert row["supported_formats_json"] == '["onnx"]'
        assert row["risk_level"] == "high"
