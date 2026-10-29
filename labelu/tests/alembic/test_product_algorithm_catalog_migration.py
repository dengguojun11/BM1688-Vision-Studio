from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[2] / "alembic_labelu" / "versions"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_catalog_migration_is_idempotent_and_reversible():
    engine = sa.create_engine("sqlite:///:memory:")
    base = _load("k0l1m2n3o4p5_add_algorithm_registry.py")
    catalog = _load("b1c2d3e4f5a6_seed_7gai_product_algorithm_catalog.py")
    with engine.begin() as bind:
        operations = Operations(MigrationContext.configure(bind))
        base.op = operations
        base.upgrade()
        catalog.op = operations
        catalog.upgrade()
        catalog.upgrade()
        assert bind.execute(
            sa.text("SELECT COUNT(*) FROM algorithm_definition WHERE metadata_json LIKE '%product%'")
        ).scalar() == 31
        assert bind.execute(
            sa.text("SELECT COUNT(*) FROM algorithm_definition WHERE metadata_json LIKE '%rule_template%'")
        ).scalar() == 3
        catalog.downgrade()
        assert bind.execute(sa.text("SELECT COUNT(*) FROM algorithm_definition")).scalar() == 1
        base.downgrade()
