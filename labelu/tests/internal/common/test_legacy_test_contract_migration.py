from __future__ import annotations

import json
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "labelu" / "docs" / "7gai_legacy_test_contract_migration.json"
ALLOWED_CATEGORIES = {
    "retired_project_control_plane",
    "phase_one_contract_to_migrate",
}
ROLE_PATTERN = re.compile(r"^R(?:0[1-9]|10)(?:/R(?:0[1-9]|10))*$")


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_legacy_contract_migration_is_machine_readable_and_complete():
    document = _load_contract()

    assert document["schema_version"] == 1
    result = document["source_run"]["result"]
    assert result == {"passed": 2303, "failed": 60, "skipped": 47}
    assert set(document["allowed_categories"]) == ALLOWED_CATEGORIES

    entries = document["entries"]
    nodeids: list[str] = []
    for entry in entries:
        test_file = REPOSITORY_ROOT / entry["test_file"]
        assert test_file.is_file(), entry["test_file"]
        assert entry["category"] in ALLOWED_CATEGORIES
        assert ROLE_PATTERN.fullmatch(entry["owner_role"]), entry["owner_role"]
        assert entry["tests"]
        assert entry["reason"]
        assert entry["action"]
        for test_name in entry["tests"]:
            nodeids.append(f"{entry['test_file']}::{test_name}")

    assert len(nodeids) == 60
    assert len(set(nodeids)) == len(nodeids)
    assert {entry["category"] for entry in entries} == ALLOWED_CATEGORIES


def test_legacy_contract_migration_keeps_category_counts_explicit():
    document = _load_contract()
    counts = {category: 0 for category in ALLOWED_CATEGORIES}
    for entry in document["entries"]:
        counts[entry["category"]] += len(entry["tests"])

    assert counts == {
        "retired_project_control_plane": 26,
        "phase_one_contract_to_migrate": 34,
    }
