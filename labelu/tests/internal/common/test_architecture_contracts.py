from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
import yaml

from labelu.internal.common.algorithm_registry import ALL_ALGORITHM_DEFINITIONS
from labelu.internal.common.event_catalog import CANONICAL_EVENT_NAME_RE, EVENT_CATALOG


def test_frozen_algorithm_catalog_counts_are_derived_from_registry():
    products = [
        item
        for item in ALL_ALGORITHM_DEFINITIONS
        if item.catalog_kind == "product"
    ]

    assert len(products) == 31
    assert sum(
        item.algorithm_kind in {"model", "pipeline"} for item in products
    ) == 28
    assert sum(item.algorithm_kind == "rule_template" for item in products) == 3
    assert sum(
        item.catalog_kind == "internal" for item in ALL_ALGORITHM_DEFINITIONS
    ) == 5


def test_alembic_revision_graph_has_one_head():
    repository_root = Path(__file__).resolve().parents[4]
    config = Config(str(repository_root / "labelu" / "alembic_labelu" / "alembic.ini"))
    config.set_main_option(
        "script_location", str(repository_root / "labelu" / "alembic_labelu")
    )

    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"Alembic graph must have one head, found: {heads}"


def test_event_catalog_yaml_matches_code_catalog():
    repository_root = Path(__file__).resolve().parents[4]
    catalog_path = repository_root / "docs" / "系统方案文档" / "event_catalog.yaml"
    document = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    yaml_names = {item["canonical"] for item in document["events"]}
    code_names = {item.name for item in EVENT_CATALOG}

    assert yaml_names == code_names
    assert all(CANONICAL_EVENT_NAME_RE.fullmatch(name) for name in yaml_names)
