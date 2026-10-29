"""一期八段候选链的空 SQLite 全链演练。"""

from labelu.scripts.rehearse_phase_one_candidate_chain import rehearse


def test_candidate_chain_upgrades_and_downgrades_without_project_dimension():
    report = rehearse()
    assert report["status"] == "通过"
    assert report["候选段数"] == 8
    assert report["project_columns"] == 0
    assert report["project_foreign_keys"] == 0
    assert report["tables"] >= 20
    assert report["降级"] == "无候选业务表残留，保留h7基线前置表"
