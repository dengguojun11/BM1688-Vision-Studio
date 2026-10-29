"""执行租约候选与正式迁移链只读对账。"""

from __future__ import annotations

from labelu.scripts.audit_execution_lease_candidate_alignment import audit_alignment


def test_candidate_contract_matches_formal_z4_but_parent_chain_blocks_direct_merge():
    report = audit_alignment()
    assert report["formal_heads"] == ["zf2a3b4c5d6e"]
    assert report["production_baseline"] == "h7i8j9k0l1m2"
    assert report["current_head_reachable_from_production_baseline"] is True
    assert report["formal_parent"] == "z3a4b5c6d7e8"
    assert report["candidate_revision"] == "gai_p1_08"
    assert report["candidate_parent"] == "gai_p1_07"
    assert report["candidate_parent_exists_in_formal_graph"] is False
    assert report["candidate_in_formal_versions"] is False
    assert report["semantic_contract_match"] is True
    assert report["direct_formal_merge_safe"] is False
    assert any("父版本" in item for item in report["minimum_blockers"])


def test_alignment_is_read_only_and_reports_all_table_diffs_as_empty():
    report = audit_alignment()
    for diff in report["column_diffs"].values():
        assert diff == {"formal_only": [], "candidate_only": []}
    for diff in report["index_diffs"].values():
        assert diff["formal_only"] == []
        assert diff["candidate_only"] == []
        assert diff["column_order_mismatch"] == {}
