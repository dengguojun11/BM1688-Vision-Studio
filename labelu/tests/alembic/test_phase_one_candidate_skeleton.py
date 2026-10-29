"""隔离八段候选骨架的结构审计，不执行任何候选DDL。"""

from __future__ import annotations

import ast
from pathlib import Path

from labelu.internal.common.phase_one_migration_rebuild_audit import (
    audit_replacement_workspace,
)

WORKSPACE = Path(__file__).parents[2].parents[0] / ".tmp" / "phase-one-rebuild" / "versions"


def _literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name}缺少{name}")


def test_skeleton_is_a_single_parent_eight_segment_chain():
    report = audit_replacement_workspace(WORKSPACE)
    assert report == {
        "production_baseline": "h7i8j9k0l1m2",
        "head": "gai_p1_08",
        "revision_count": 8,
    }


def test_skeleton_declares_unique_table_ownership_and_explicit_blockers():
    owners: dict[str, str] = {}
    for path in sorted(WORKSPACE.glob("gai_p1_*.py")):
        revision = _literal(path, "revision")
        scope = _literal(path, "TABLE_SCOPE")
        blockers = _literal(path, "BLOCKERS")
        assert scope, revision
        assert blockers, revision
        for table_name in scope:
            previous = owners.setdefault(table_name, revision)
            assert previous == revision, f"{table_name}重复归属：{previous}/{revision}"

    assert owners["yolo_bm1688_export"] == "gai_p1_08"
    assert owners["yolo_edge_validation_job"] == "gai_p1_08"
    assert owners["dataset"] == "gai_p1_01"


def test_skeleton_upgrade_is_explicitly_scoped_and_blocked_for_production():
    path = WORKSPACE / "gai_p1_08_compatibility.py"
    text = path.read_text(encoding="utf-8")
    assert "尚未合入完整第08段" in text
    assert "Task/附件/数据源/专家回流兼容字段与model_version_id尚未完成" in text
    assert "def upgrade" in text
    assert "def downgrade" in text


def test_first_two_segments_are_static_and_do_not_copy_orm_metadata():
    first = (WORKSPACE / "gai_p1_01_data_contract.py").read_text(encoding="utf-8")
    second = (WORKSPACE / "gai_p1_02_model_lineage.py").read_text(encoding="utf-8")
    for source in (first, second):
        assert "create_all" not in source
        assert "phase_one_tables" not in source
        assert "Base.metadata" not in source
        assert "op.create_table" in source
        assert "DEPENDENCIES" in source


def test_execution_and_ingestion_segments_are_static_and_project_free():
    for revision in ("gai_p1_03_execution.py", "gai_p1_04_ingestion_media.py"):
        source = (WORKSPACE / revision).read_text(encoding="utf-8")
        assert "create_all" not in source
        assert "Base.metadata" not in source
        assert 'Column("project_id"' not in source
        assert "op.create_table" in source
        assert "DEPENDENCIES" in source


def test_ingestion_segment_has_no_scene_dimension_and_marks_credentials_as_ciphertext():
    source = (WORKSPACE / "gai_p1_04_ingestion_media.py").read_text(encoding="utf-8")
    assert "scene_id" not in source
    assert "ix_media_asset_scene_id" not in source
    assert 'comment="应用层加密密文，禁止写入明文"' in source
    assert "secret_ref契约" in source
